"""Candidate scoring (step 8; §6.8) — bounded LLM sub-scores + deterministic combine.

The LLM emits **sub-scores only** (``skill_match_score``, ``feedback_score``), a short narrative,
and verbatim ``EvidenceCitation``s, through a typed DSPy ``Signature`` over ``PseudonymisedLM``
(``temperature=0``, injected as a ``predict`` seam so tests mock it). **Python** then:

- combines: ``combined_score = weights.skill·skill_match + weights.feedback·feedback`` (AD-030,
  weights from config) — the LLM never does the arithmetic (tech.md rule 4);
- computes ``hard_skill_coverage`` by **exact** name membership — a hard skill is never credited
  via adjacency (AD-033, enforced here regardless of LLM output);
- computes ``desired_skill_coverage`` with adjacency partial credit (exact 1.0 / adjacent 0.5 /
  else 0, via ``config.adjacency_map``, AD-033/035), firing ``ADJACENCY_USED`` only when credit was
  actually awarded;
- verifies every citation quote is verbatim-present in the candidate's source text and **drops**
  the rest (AD-073) — no fabricated rationale survives;
- raises the trade-off ``Flag``s (new-joiner, roll-off, retention, freshness-warn).

Candidate text reaching the LLM is PII-free by construction (ingestion redacted it; the hydrated
``Candidate`` carries ``candidate_id`` in place of name/email, AD-091). An LLM error on one
candidate → ``None`` (the orchestrator skips + counts it; the rest still rank, §6.8).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import dspy
import structlog

from dsm.config import load_prompt
from dsm.match.freshness import WARN, FreshnessVerdict
from dsm.match.models import ScoreExtraction
from dsm.models import (
    Candidate,
    CandidateAssessment,
    CandidateSource,
    EvidenceCitation,
    Flag,
    FlagType,
    NearMiss,
    RollingOff,
    TargetProfileScorecard,
)

_log = structlog.get_logger("dsm.match.score")

# Injected LLM seam: (scorecard, candidate) → sub-scores + narrative + citations (mocked in tests).
ScorePredictor = Callable[[TargetProfileScorecard, Candidate], ScoreExtraction]

_WS = re.compile(r"\s+")


class CandidateScoring(dspy.Signature):
    """Assess a candidate against a role; emit sub-scores + cited evidence (config/prompts)."""

    role: TargetProfileScorecard = dspy.InputField()
    candidate_skills: list[str] = dspy.InputField()
    candidate_feedback: list[str] = dspy.InputField()
    profile_summary: str = dspy.InputField()
    assessment: ScoreExtraction = dspy.OutputField()


def make_score_predictor(lm: dspy.LM) -> ScorePredictor:
    """Build the real score predictor over ``PseudonymisedLM`` (used by the CLI, not tests)."""
    sig = CandidateScoring.with_instructions(load_prompt("candidate_scoring"))
    predictor = dspy.Predict(sig)

    def _predict(scorecard: TargetProfileScorecard, candidate: Candidate) -> ScoreExtraction:
        with dspy.context(lm=lm):
            return predictor(
                role=scorecard,
                candidate_skills=[f"{s.name} {s.proficiency.value}" for s in candidate.skills],
                candidate_feedback=[e.text for e in candidate.feedback.entries],
                profile_summary=candidate.profile_summary or "",
            ).assessment

    return _predict


# Injected seam for the no-match path: (scorecard, candidate, gap_summary) → rationale (AD-098).
NearMissRationalePredictor = Callable[[TargetProfileScorecard, Candidate, str], str]


class NearMissRationale(dspy.Signature):
    """Explain why a near-miss is worth considering once its gap is resolved (config/prompts)."""

    role: TargetProfileScorecard = dspy.InputField()
    candidate_skills: list[str] = dspy.InputField()
    candidate_feedback: list[str] = dspy.InputField()
    gap: str = dspy.InputField()
    rationale: str = dspy.OutputField()


def make_near_miss_rationale_predictor(lm: dspy.LM) -> NearMissRationalePredictor:
    """Build the near-miss rationale predictor over ``PseudonymisedLM`` (CLI only, not tests)."""
    sig = NearMissRationale.with_instructions(load_prompt("near_miss_rationale"))
    predictor = dspy.Predict(sig)

    def _predict(scorecard: TargetProfileScorecard, candidate: Candidate, gap: str) -> str:
        with dspy.context(lm=lm):
            return predictor(
                role=scorecard,
                candidate_skills=[f"{s.name} {s.proficiency.value}" for s in candidate.skills],
                candidate_feedback=[e.text for e in candidate.feedback.entries],
                gap=gap,
            ).rationale

    return _predict


def explain_near_misses(
    near_misses: list[NearMiss],
    by_email: dict[str, Candidate],
    scorecard: TargetProfileScorecard,
    predict: NearMissRationalePredictor,
) -> list[NearMiss]:
    """Attach an LLM ``selection_rationale`` to each near-miss (AD-098).

    Only the near-misses passed in are explained — the caller passes the shown top-3, so we never
    pay for misses beyond the AD-063d cap. PII-free by construction: the predictor sees only the
    candidate's skills + feedback + the gap, never name/email (Golden rule 3). A predictor failure
    is logged and leaves ``selection_rationale=None`` — the near-miss is still returned.
    """
    explained: list[NearMiss] = []
    for near_miss in near_misses:
        candidate = by_email.get(near_miss.candidate_email)
        if candidate is None:
            explained.append(near_miss)
            continue
        try:
            rationale = predict(scorecard, candidate, near_miss.gap_summary)
        except Exception:  # noqa: BLE001 — a rationale failure must never drop the near-miss
            _log.warning("near_miss_rationale_failed", candidate=near_miss.candidate_email)
            explained.append(near_miss)
            continue
        explained.append(near_miss.model_copy(update={"selection_rationale": rationale}))
    return explained


def _norm(text: str) -> str:
    """Whitespace-normalized form for verbatim comparison (mirrors enrich; match ⊥ ingest)."""
    return _WS.sub(" ", text).strip()


def _candidate_source(candidate: Candidate) -> str:
    """The candidate's own facts a citation may quote: skill names + feedback text + summary."""
    parts = [s.name for s in candidate.skills]
    parts.extend(entry.text for entry in candidate.feedback.entries)
    if candidate.profile_summary:
        parts.append(candidate.profile_summary)
    return " ".join(parts)


def _verified_citations(
    citations: list[EvidenceCitation], candidate: Candidate
) -> list[EvidenceCitation]:
    """Keep only citations whose quote is verbatim-present in the candidate source (AD-073)."""
    source = _norm(_candidate_source(candidate))
    kept: list[EvidenceCitation] = []
    for citation in citations:
        quote = _norm(citation.text)
        if quote and quote in source:
            kept.append(citation)
        else:
            _log.warning("score.citation_dropped", candidate_id=candidate.email)
    return kept


def _hard_skill_coverage(candidate: Candidate, scorecard: TargetProfileScorecard) -> float:
    """Fraction of hard skills the candidate holds by **exact** name — never adjacency (AD-033)."""
    hard = scorecard.hard_depth_skills
    if not hard:
        return 1.0
    held = {skill.name for skill in candidate.skills}
    return sum(1 for req in hard if req.name in held) / len(hard)


def _desired_skill_coverage(
    candidate: Candidate, scorecard: TargetProfileScorecard, adjacency_map: dict[str, list[str]]
) -> tuple[float, bool]:
    """Desired-skill coverage with adjacency partial credit (AD-033/035).

    Per desired skill: exact hold = 1.0, an adjacent skill held (via ``adjacency_map``) = 0.5, else
    0; averaged over the desired skills (1.0 when there are none). Returns ``(coverage,
    adjacency_used)`` — ``adjacency_used`` is True only when partial credit was actually awarded.
    """
    desired = scorecard.desired_skills
    if not desired:
        return 1.0, False
    held = {skill.name for skill in candidate.skills}
    total = 0.0
    adjacency_used = False
    for req in desired:
        if req.name in held:
            total += 1.0
        elif any(adjacent in held for adjacent in adjacency_map.get(req.name, [])):
            total += 0.5
            adjacency_used = True
    return total / len(desired), adjacency_used


def _flags(
    candidate: Candidate, adjacency_used: bool, freshness: FreshnessVerdict | None
) -> list[Flag]:
    """Surface the trade-offs as ``Flag``s — shown, never silently re-ranked."""
    flags: list[Flag] = []
    if adjacency_used:
        flags.append(
            Flag(
                type=FlagType.ADJACENCY_USED,
                message="Desired-skill credit awarded via an adjacent skill.",
            )
        )
    if candidate.source is CandidateSource.NEW_JOINER:
        flags.append(
            Flag(
                type=FlagType.UNVERIFIED_SKILLS,
                message="New joiner — skills not yet demonstrated at EE.",
            )
        )
    availability = candidate.availability
    if isinstance(availability, RollingOff) and availability.confidence == "low":
        flags.append(
            Flag(
                type=FlagType.ROLL_OFF_UNCERTAIN,
                message="Roll-off date is low-confidence and may slip.",
            )
        )
    if any(entry.retention_flag for entry in candidate.feedback.entries):
        flags.append(
            Flag(
                type=FlagType.RETENTION_RISK,
                message="Client feedback asks to retain — staffing may face pushback.",
            )
        )
    if freshness is not None and freshness.action == WARN:
        flags.append(Flag(type=FlagType.FRESHNESS_WARNING, message=freshness.message))
    return flags


def score_candidate(
    candidate: Candidate,
    scorecard: TargetProfileScorecard,
    *,
    predict: ScorePredictor,
    config: dict[str, Any],
    freshness: FreshnessVerdict | None = None,
) -> CandidateAssessment | None:
    """Score one candidate against the role (§6.8). ``None`` if the LLM errors on this candidate.

    Args:
        candidate: the hydrated serving candidate (skills/feedback/summary; ``email`` is the cid).
        scorecard: the clarified role.
        predict: the injected LLM seam emitting sub-scores + narrative + citations.
        config: runtime config — ``weights.skill``/``weights.feedback`` (AD-030) + adjacency_map.
        freshness: the run's freshness verdict; a ``warn`` attaches a freshness flag (AD-092).

    Returns:
        A ``CandidateAssessment`` with the deterministic combine + coverages + verified citations +
        flags, or ``None`` when the LLM call fails (the orchestrator logs/counts and skips it).
    """
    try:
        extraction = predict(scorecard, candidate)
    except Exception as exc:  # noqa: BLE001 — one bad candidate must not kill the batch (§6.8)
        # A leak-scan trip is a PII-boundary failure, not a flaky-call skip: redaction let a known
        # identifier through, which is a logic bug affecting every candidate, not data variance.
        # Propagate it (halt loudly, like ingest) instead of burying it as a per-candidate skip.
        # Matched by class name because `dsm.match` must not import `dsm.pii` (boundary at the
        # CLI; AD-101). `PIILeakError` is `dsm.pii.leakscan`'s outbound hard gate (AD-069).
        if type(exc).__name__ == "PIILeakError":
            _log.error("score.pii_leak_block", candidate_id=candidate.email)
            raise
        _log.warning("score.failed_skip", candidate_id=candidate.email, reason=type(exc).__name__)
        return None

    weights = config["weights"]
    combined_score = (
        float(weights["skill"]) * extraction.skill_match_score
        + float(weights["feedback"]) * extraction.feedback_score
    )
    hard_coverage = _hard_skill_coverage(candidate, scorecard)
    desired_coverage, adjacency_used = _desired_skill_coverage(
        candidate, scorecard, config.get("adjacency_map", {})
    )

    return CandidateAssessment(
        candidate=candidate,
        skill_match_score=extraction.skill_match_score,
        feedback_score=extraction.feedback_score,
        combined_score=combined_score,
        flags=_flags(candidate, adjacency_used, freshness),
        evidence=_verified_citations(extraction.evidence, candidate),
        narrative=extraction.narrative,
        hard_skill_coverage=hard_coverage,
        desired_skill_coverage=desired_coverage,
    )
