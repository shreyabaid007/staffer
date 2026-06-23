"""Six code-based invariant evaluators for the query-time pipeline (c-002, AD-095 Tier 1).

Each function takes a pipeline result + context and returns an ``InvariantResult``.
Pure, importable, no test-framework imports. Reuses ``dsm.pii.leakscan`` for no-PII-leak.

No LLM judge — these are objective properties of the output (AD-095).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from dsm.models import (
    Candidate,
    ExclusionLog,
    ExclusionReason,
    FlagType,
    NoMatchResult,
    ShortlistResult,
    TargetProfileScorecard,
)

MatchResult = ShortlistResult | NoMatchResult

_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    """Whitespace-normalised form for verbatim comparison (mirrors score.py; eval ⊥ match)."""
    return _WS.sub(" ", text).strip()


def _candidate_source(candidate: Candidate) -> str:
    """The candidate's quotable text: skill names + feedback text + summary."""
    parts: list[str] = [s.name for s in candidate.skills]
    parts.extend(e.text for e in candidate.feedback.entries)
    if candidate.profile_summary:
        parts.append(candidate.profile_summary)
    return " ".join(parts)


@dataclass(frozen=True)
class InvariantResult:
    """Pass/fail with a human-readable reason."""

    passed: bool
    reason: str


@dataclass
class SeamInputs:
    """Captured arguments passed to predict/embed/rerank seams during an eval run."""

    clarify_inputs: list[dict[str, Any]] = field(default_factory=list)
    score_inputs: list[dict[str, Any]] = field(default_factory=list)
    embed_inputs: list[str] = field(default_factory=list)
    rerank_inputs: list[tuple[str, list[str]]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 1. gates-respected
# ---------------------------------------------------------------------------


def gates_respected(
    result: MatchResult,
    *,
    exclusion_log: ExclusionLog | None = None,
) -> InvariantResult:
    """No excluded candidate appears in ranked_assessments."""
    log = exclusion_log or result.exclusion_log
    excluded_emails = {e.candidate_email for e in log.exclusions}

    if isinstance(result, NoMatchResult):
        return InvariantResult(passed=True, reason="NoMatchResult — no ranked candidates.")

    ranked_emails = {a.candidate.email for a in result.ranked_assessments}
    overlap = excluded_emails & ranked_emails
    if overlap:
        reasons = {
            e.candidate_email: e.reason.value
            for e in log.exclusions
            if e.candidate_email in overlap
        }
        detail = ", ".join(f"{email} ({reasons.get(email, '?')})" for email in sorted(overlap))
        return InvariantResult(passed=False, reason=f"Excluded candidates ranked: {detail}")
    return InvariantResult(passed=True, reason="No excluded candidate appears in the shortlist.")


# ---------------------------------------------------------------------------
# 2. hard-skill-not-cleared-by-adjacency
# ---------------------------------------------------------------------------


def hard_skill_not_cleared_by_adjacency(
    result: ShortlistResult,
    *,
    exclusion_log: ExclusionLog | None = None,
    scorecard: TargetProfileScorecard,
    adjacency_map: dict[str, list[str]],
) -> InvariantResult:
    """An adjacent-but-missing-hard-skill candidate is excluded, never ranked."""
    log = exclusion_log or result.exclusion_log
    hard_names = {sr.name.lower() for sr in scorecard.hard_depth_skills}
    adjacent_to_hard = set()
    for name in hard_names:
        adjacent_to_hard.update(adj.lower() for adj in adjacency_map.get(name, []))

    hard_excluded = [e for e in log.exclusions if e.reason is ExclusionReason.HARD_SKILL_MISMATCH]
    ranked_emails = {a.candidate.email for a in result.ranked_assessments}

    violations: list[str] = []
    for exc in hard_excluded:
        if exc.candidate_email in ranked_emails:
            violations.append(exc.candidate_email)

    if violations:
        return InvariantResult(
            passed=False,
            reason=(
                f"Hard-skill-excluded candidates ranked: {', '.join(sorted(violations))}. "
                "Adjacency must not clear a hard skill."
            ),
        )
    return InvariantResult(
        passed=True, reason="No hard-skill-excluded candidate appears in the shortlist."
    )


# ---------------------------------------------------------------------------
# 3. evidence-cited
# ---------------------------------------------------------------------------


def evidence_cited(result: ShortlistResult) -> InvariantResult:
    """Every evidence quote is verbatim-present in the candidate source text."""
    failures: list[str] = []
    for assessment in result.ranked_assessments:
        source = _norm(_candidate_source(assessment.candidate))
        for ev in assessment.evidence:
            if _norm(ev.text) not in source:
                failures.append(
                    f"{assessment.candidate.email}: quote not in source: '{ev.text[:60]}...'"
                    if len(ev.text) > 60
                    else f"{assessment.candidate.email}: quote not in source: '{ev.text}'"
                )
    if failures:
        return InvariantResult(passed=False, reason="; ".join(failures))
    return InvariantResult(passed=True, reason="All citations verified in source text.")


# ---------------------------------------------------------------------------
# 4. no-PII-leak (structural only — stub anonymiser limitation)
# ---------------------------------------------------------------------------


def no_pii_leak(
    result: MatchResult,
    *,
    seam_inputs: SeamInputs | None = None,
    known_pii: list[str] | None = None,
) -> InvariantResult:
    """No raw name/email reaches seam inputs or output narratives.

    **Structural only** — because ``PseudonymisedLM`` is still a pass-through stub,
    this invariant verifies seam inputs are capability/``candidate_id``-only. It does
    NOT yet exercise a real anonymiser. TODO: tighten when the live anonymiser lands.
    """
    failures: list[str] = []
    pii_strings = [s.lower() for s in (known_pii or []) if s.strip()]

    if isinstance(result, ShortlistResult):
        for assessment in result.ranked_assessments:
            narrative_lower = assessment.narrative.lower()
            for pii in pii_strings:
                if pii in narrative_lower:
                    failures.append(
                        f"PII '{pii}' found in narrative for {assessment.candidate.email}"
                    )

    if seam_inputs and pii_strings:
        from dsm.pii.leakscan import leak_scan

        all_texts: list[str] = []
        for si in seam_inputs.score_inputs:
            all_texts.extend(str(v) for v in si.values())
        for si in seam_inputs.clarify_inputs:
            all_texts.extend(str(v) for v in si.values())
        all_texts.extend(seam_inputs.embed_inputs)
        for query, passages in seam_inputs.rerank_inputs:
            all_texts.append(query)
            all_texts.extend(passages)

        combined = " ".join(all_texts)
        scan = leak_scan(combined, known_pii=known_pii or [])
        if not scan.clean:
            failures.append(f"PII leaked into seam inputs: {len(scan.hits)} hit(s)")

    if failures:
        return InvariantResult(passed=False, reason="; ".join(failures))
    return InvariantResult(passed=True, reason="No PII leak detected (structural check).")


# ---------------------------------------------------------------------------
# 5. determinism (ordering/seed invariance)
# ---------------------------------------------------------------------------


def determinism(
    run_fn: Callable[..., ShortlistResult | NoMatchResult],
    *,
    candidates: list[Candidate],
    scorecard: TargetProfileScorecard,
    run_kwargs: dict[str, Any],
    n_trials: int = 3,
) -> InvariantResult:
    """Shuffled candidate order → byte-identical output.

    The cassette LM holds the LLM fixed; this isolates the deterministic plumbing
    (Python combine, sort, tie-break). It does NOT test live-model ``temperature=0``
    reproducibility — that is a Tier-3 concern (the cassette drift-guard).
    """
    import random

    baseline = run_fn(candidates, scorecard, **run_kwargs)
    baseline_json = baseline.model_dump_json()

    for i in range(n_trials):
        shuffled = list(candidates)
        random.Random(42 + i).shuffle(shuffled)
        trial = run_fn(shuffled, scorecard, **run_kwargs)
        trial_json = trial.model_dump_json()
        if trial_json != baseline_json:
            return InvariantResult(
                passed=False,
                reason=f"Trial {i + 1}: output differs after candidate shuffle.",
            )

    return InvariantResult(
        passed=True,
        reason=f"Output byte-identical across {n_trials} shuffled-input trials.",
    )


# ---------------------------------------------------------------------------
# 6. adjacency-flag
# ---------------------------------------------------------------------------


def adjacency_flag(
    result: ShortlistResult,
    *,
    scorecard: TargetProfileScorecard,
    adjacency_map: dict[str, list[str]],
) -> InvariantResult:
    """ADJACENCY_USED present iff adjacency credit was awarded to a desired skill."""
    desired_names = {sr.name.lower() for sr in scorecard.desired_skills}
    if not desired_names:
        for assessment in result.ranked_assessments:
            if any(f.type is FlagType.ADJACENCY_USED for f in assessment.flags):
                return InvariantResult(
                    passed=False,
                    reason=(
                        f"{assessment.candidate.email}: ADJACENCY_USED flag present "
                        "but no desired skills in scorecard."
                    ),
                )
        return InvariantResult(
            passed=True, reason="No desired skills — ADJACENCY_USED correctly absent."
        )

    failures: list[str] = []
    for assessment in result.ranked_assessments:
        candidate_skills = {s.name.lower() for s in assessment.candidate.skills}
        credit_awarded = False
        for desired in desired_names:
            if desired in candidate_skills:
                continue
            adjacents = {a.lower() for a in adjacency_map.get(desired, [])}
            if candidate_skills & adjacents:
                credit_awarded = True
                break

        has_flag = any(f.type is FlagType.ADJACENCY_USED for f in assessment.flags)
        if credit_awarded and not has_flag:
            failures.append(
                f"{assessment.candidate.email}: adjacency credit awarded "
                "but ADJACENCY_USED missing"
            )
        elif has_flag and not credit_awarded:
            failures.append(
                f"{assessment.candidate.email}: ADJACENCY_USED present but no adjacency credit"
            )

    if failures:
        return InvariantResult(passed=False, reason="; ".join(failures))
    return InvariantResult(passed=True, reason="ADJACENCY_USED flag matches credit for all.")
