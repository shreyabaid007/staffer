"""Enrich — LLM extraction behind the PII boundary (§5 step 4, §6 Phase 4, §9, §10).

The pipeline for one resume/feedback silver record:
``anonymize → leak-scan (gate) → LLM extract (DSPy, temp 0) → de-anonymize → verify citations``

- **anonymize/gate (AD-069):** ``redact`` strips known identifiers + NER residuals;
  ``assert_no_leak`` blocks the call and fails the build if any known PII survived. The LLM only
  ever sees redacted, scanned text — no PII reaches OpenRouter, ever.
- **extract:** through ``PseudonymisedLM`` only, via typed DSPy ``Signature``s — no raw prompt
  strings (tech.md). The LLM call is injected as a ``predict`` callable so unit tests use recorded
  cassettes (no live network in ``make check``); real predictors come from ``make_*_predictor``.
- **de-anonymize:** restore originals into the structured output via the in-memory mapping.
- **verify (AD-073):** every ``EvidenceCitation.text`` must exist verbatim in the *original*
  source; a fact whose quote is absent is dropped + logged + counted (the rest stands).

Determinism: temp 0 + cassettes in tests; version stamps (``prompt_version``/``model_version``) are
applied at gold (merge), not here. No response cache this slice (EN-6).
"""

from __future__ import annotations

import re
from collections.abc import Callable

import dspy
import structlog

from dsm.config import load_prompt
from dsm.ingest.lineage import RunMetrics, log_citation_verify_failure
from dsm.ingest.models import (
    FeedbackExtraction,
    NormalizedRecord,
    ProfileSummaryExtraction,
    SkillExtraction,
)
from dsm.models import EvidenceCitation
from dsm.pii.leakscan import assert_no_leak
from dsm.pii.redact import NerFn, RedactionResult, deanonymize, redact

_log = structlog.get_logger("dsm.ingest.enrich")

# Injected LLM seam: operates on already-anonymized text, returns the typed extraction.
ResumePredictor = Callable[[str, list[str]], ProfileSummaryExtraction]
FeedbackPredictor = Callable[[str], FeedbackExtraction]

_WS = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# DSPy signatures (typed; instructions are the versioned config/prompts/*)
# ---------------------------------------------------------------------------


class ProfileExtraction(dspy.Signature):
    """Extract structured profile facts from an anonymized resume (see config/prompts)."""

    resume_text: str = dspy.InputField()
    sections: list[str] = dspy.InputField()
    extraction: ProfileSummaryExtraction = dspy.OutputField()


class FeedbackItemExtraction(dspy.Signature):
    """Extract structured signals from one anonymized feedback item (see config/prompts)."""

    feedback_text: str = dspy.InputField()
    extraction: FeedbackExtraction = dspy.OutputField()


def make_resume_predictor(lm: dspy.LM) -> ResumePredictor:
    """Build the real resume predictor over ``PseudonymisedLM`` (used by the CLI, not tests)."""
    sig = ProfileExtraction.with_instructions(load_prompt("profile_extraction"))
    predictor = dspy.Predict(sig)

    def _predict(resume_text: str, sections: list[str]) -> ProfileSummaryExtraction:
        with dspy.context(lm=lm):
            return predictor(resume_text=resume_text, sections=sections).extraction

    return _predict


def make_feedback_predictor(lm: dspy.LM) -> FeedbackPredictor:
    """Build the real feedback predictor over ``PseudonymisedLM`` (used by the CLI, not tests)."""
    sig = FeedbackItemExtraction.with_instructions(load_prompt("feedback_extraction"))
    predictor = dspy.Predict(sig)

    def _predict(feedback_text: str) -> FeedbackExtraction:
        with dspy.context(lm=lm):
            return predictor(feedback_text=feedback_text).extraction

    return _predict


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def _anonymize_and_gate(text: str, known_pii: list[str], ner: NerFn | None) -> RedactionResult:
    """Redact then leak-scan; raises ``PIILeakError`` if any known PII survived (PII-5)."""
    result = redact(text, known_pii=known_pii, ner=ner)
    assert_no_leak(result.text, known_pii=known_pii)  # hard gate before the LLM ever sees it
    return result


def _norm(text: str) -> str:
    """Whitespace-normalized form for verbatim comparison (deterministic)."""
    return _WS.sub(" ", text).strip()


def _quote_present(quote: str, source: str) -> bool:
    """True if the (de-anonymized) quote exists verbatim in the source, modulo whitespace."""
    q = _norm(quote)
    return bool(q) and q in _norm(source)


def _deanon_citation(cite: EvidenceCitation, mapping: dict[str, str]) -> EvidenceCitation:
    return cite.model_copy(update={"text": deanonymize(cite.text, mapping)})


def enrich_resume(
    record: NormalizedRecord,
    *,
    known_pii: list[str],
    predict: ResumePredictor,
    run_id: str = "",
    ner: NerFn | None = None,
    metrics: RunMetrics | None = None,
) -> ProfileSummaryExtraction | None:
    """Resume → ``ProfileSummaryExtraction`` (cited, verified). ``None`` if extraction is unusable.

    Drops any skill whose citation quote is not verbatim-present in the source (EN-4); keeps the
    rest. Returns ``None`` + logs on a schema-invalid LLM response (EN-7).
    """
    if not record.raw_text:
        return None
    source = record.raw_text
    sections = _sections_of(record)
    redacted = _anonymize_and_gate(source, known_pii, ner)

    try:
        raw = predict(redacted.text, sections)
    except Exception as exc:  # schema-invalid / LLM error → skip+count, never crash (EN-7)
        _log.warning(
            "enrich.extract_failed",
            run_id=run_id,
            candidate_id=record.candidate_id,
            source_hash=record.source_hash,
            reason=type(exc).__name__,
        )
        return None

    kept: list[SkillExtraction] = []
    for skill in raw.skills:
        cite = _deanon_citation(skill.evidence, redacted.mapping)
        if _quote_present(cite.text, source):
            kept.append(skill.model_copy(update={"evidence": cite}))
        else:
            log_citation_verify_failure(
                run_id=run_id,
                candidate_id=record.candidate_id,
                source_hash=record.source_hash,
                fact=skill.name,
                metrics=metrics,
            )
    return raw.model_copy(
        update={
            "skills": kept,
            "employers": [deanonymize(e, redacted.mapping) for e in raw.employers],
            "projects": [deanonymize(p, redacted.mapping) for p in raw.projects],
            "domains": [deanonymize(d, redacted.mapping) for d in raw.domains],
        }
    )


def enrich_feedback(
    record: NormalizedRecord,
    *,
    known_pii: list[str],
    predict: FeedbackPredictor,
    run_id: str = "",
    ner: NerFn | None = None,
    metrics: RunMetrics | None = None,
) -> FeedbackExtraction | None:
    """Feedback item → ``FeedbackExtraction`` (cited, verified). ``None`` if the item is rejected.

    A feedback item carries a single evidence citation; if its quote is not verbatim-present, the
    whole item is rejected + logged + counted (EN-4). ``None`` + log on schema-invalid output.
    """
    if not record.raw_text:
        return None
    source = record.raw_text
    redacted = _anonymize_and_gate(source, known_pii, ner)

    try:
        raw = predict(redacted.text)
    except Exception as exc:
        _log.warning(
            "enrich.extract_failed",
            run_id=run_id,
            candidate_id=record.candidate_id,
            source_hash=record.source_hash,
            reason=type(exc).__name__,
        )
        return None

    cite = _deanon_citation(raw.evidence, redacted.mapping)
    if not _quote_present(cite.text, source):
        log_citation_verify_failure(
            run_id=run_id,
            candidate_id=record.candidate_id,
            source_hash=record.source_hash,
            fact="feedback",
            metrics=metrics,
        )
        return None
    return raw.model_copy(
        update={
            "evidence": cite,
            "confirmed_skills": [deanonymize(s, redacted.mapping) for s in raw.confirmed_skills],
            "skill_gaps": [deanonymize(s, redacted.mapping) for s in raw.skill_gaps],
            "summary": deanonymize(raw.summary, redacted.mapping),
        }
    )


def _sections_of(record: NormalizedRecord) -> list[str]:
    """Resume section labels are not on the silver record; default to empty for now."""
    return []
