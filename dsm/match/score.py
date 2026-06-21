"""Candidate scoring — DSPy sub-scores + deterministic combine (§6.8, B-002 FR-5).

Two modes:
1. **LLM mode** (``lm`` provided): bounded DSPy ``ScoreSignature`` extracts ``skill_match_score``
   and ``feedback_score``; citations are verified verbatim against the candidate's source text.
2. **Stub mode** (``lm=None``): fixed sub-scores for testing / no-LLM runs.

Combine is always deterministic: ``w_skill * skill_match + w_feedback * feedback``
(default 0.7 / 0.3, AD-030). Hard/desired coverage and adjacency partial credit
(AD-033/035) are computed in plain Python. Flags are deterministic. Returns ``None``
on LLM error so the caller can filter.
"""

from __future__ import annotations

import json

import dspy
import structlog

from dsm.config import load_prompt
from dsm.match.freshness import WARN, FreshnessVerdict
from dsm.models import (
    Candidate,
    CandidateAssessment,
    CandidateSource,
    EvidenceCitation,
    EvidenceSource,
    Flag,
    FlagType,
    ProficiencyLevel,
    RollingOff,
    SkillRequirement,
    TargetProfileScorecard,
)

_log = structlog.get_logger("dsm.match.score")

# ---------------------------------------------------------------------------
# DSPy signature
# ---------------------------------------------------------------------------


class ScoreSignature(dspy.Signature):
    """Score a candidate against a role. Cite all claims with verbatim quotes."""

    role_requirements: str = dspy.InputField()
    candidate_profile: str = dspy.InputField()
    candidate_feedback: str = dspy.InputField()
    skill_match_score: float = dspy.OutputField(desc="0.0-1.0")
    feedback_score: float = dspy.OutputField(desc="0.0-1.0")
    narrative: str = dspy.OutputField(desc="1-2 sentence explanation")
    evidence: str = dspy.OutputField(desc="JSON array of {source, text} citations")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROFICIENCY_ORDER: tuple[ProficiencyLevel, ...] = (
    ProficiencyLevel.BEGINNER,
    ProficiencyLevel.INTERMEDIATE,
    ProficiencyLevel.ADVANCED,
    ProficiencyLevel.EXPERT,
)
_PROFICIENCY_RANK = {level: index for index, level in enumerate(_PROFICIENCY_ORDER)}


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _hard_skill_coverage(
    candidate: Candidate,
    hard_skills: list[SkillRequirement],
) -> float:
    """Fraction of hard skills the candidate holds at or above the floor."""
    if not hard_skills:
        return 1.0
    held = {s.name: s.proficiency for s in candidate.skills}
    matched = 0
    for req in hard_skills:
        if req.name not in held:
            continue
        floor = req.min_proficiency
        if floor is None or _PROFICIENCY_RANK[held[req.name]] >= _PROFICIENCY_RANK[floor]:
            matched += 1
    return matched / len(hard_skills)


def _desired_skill_coverage(
    candidate: Candidate,
    desired_skills: list[SkillRequirement],
    adjacency_map: dict[str, list[str]],
) -> tuple[float, bool]:
    """Compute desired-skill coverage with adjacency partial credit (AD-033/035).

    Returns:
        (coverage_fraction, adjacency_used). Exact match = 1.0, adjacent = 0.5, else 0.
    """
    if not desired_skills:
        return 1.0, False
    held_names = {s.name for s in candidate.skills}
    total = 0.0
    adjacency_used = False
    for req in desired_skills:
        if req.name in held_names:
            total += 1.0
        else:
            adjacents = adjacency_map.get(req.name, [])
            if any(adj in held_names for adj in adjacents):
                total += 0.5
                adjacency_used = True
    return total / len(desired_skills), adjacency_used


def _verify_citations(
    raw_evidence: str,
    source_text: str,
) -> list[EvidenceCitation]:
    """Parse and verify evidence citations (AD-073).

    Each citation's ``text`` must be present verbatim (modulo whitespace) in the source.
    Unverifiable quotes are dropped.
    """
    try:
        items = json.loads(raw_evidence)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(items, list):
        return []

    verified: list[EvidenceCitation] = []
    norm_source = " ".join(source_text.split())
    for item in items:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("text", "")).strip()
        source_label = str(item.get("source", "supply_sheet")).strip()
        if not quote:
            continue
        norm_quote = " ".join(quote.split())
        if norm_quote in norm_source:
            source_enum = _parse_evidence_source(source_label)
            verified.append(EvidenceCitation(source=source_enum, text=quote))
    return verified


def _parse_evidence_source(label: str) -> EvidenceSource:
    try:
        return EvidenceSource(label)
    except ValueError:
        return EvidenceSource.SUPPLY_SHEET


def _build_source_text(candidate: Candidate) -> str:
    """Concatenate all candidate text for citation verification."""
    parts: list[str] = []
    if candidate.profile_summary:
        parts.append(candidate.profile_summary)
    for entry in candidate.feedback.entries:
        parts.append(entry.text)
    return " ".join(parts)


def _build_flags(
    candidate: Candidate,
    adjacency_used: bool,
    freshness_verdict: FreshnessVerdict | None,
) -> list[Flag]:
    """Deterministic flag generation from candidate state."""
    flags: list[Flag] = []

    if candidate.source == CandidateSource.NEW_JOINER:
        flags.append(
            Flag(
                type=FlagType.UNVERIFIED_SKILLS,
                message="New joiner — skills are self-reported, not yet demonstrated.",
            )
        )

    if isinstance(candidate.availability, RollingOff) and candidate.availability.confidence in (
        "low",
        "medium",
    ):
        flags.append(
            Flag(
                type=FlagType.ROLL_OFF_UNCERTAIN,
                message=(
                    f"Roll-off confidence is {candidate.availability.confidence}; "
                    f"expected date {candidate.availability.expected_date} may shift."
                ),
            )
        )

    if any(entry.retention_flag for entry in candidate.feedback.entries):
        flags.append(
            Flag(
                type=FlagType.RETENTION_RISK,
                message="Client has expressed interest in retaining this consultant.",
            )
        )

    if adjacency_used:
        flags.append(
            Flag(
                type=FlagType.ADJACENCY_USED,
                message="Partial credit awarded for adjacent (not exact) desired skills.",
            )
        )

    if freshness_verdict is not None and freshness_verdict.action == WARN:
        flags.append(
            Flag(
                type=FlagType.FRESHNESS_WARNING,
                message=freshness_verdict.message,
            )
        )

    return flags


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def score_candidate(
    candidate: Candidate,
    scorecard: TargetProfileScorecard,
    *,
    lm: dspy.LM | None = None,
    adjacency_map: dict[str, list[str]] | None = None,
    weights: dict[str, float] | None = None,
    freshness_verdict: FreshnessVerdict | None = None,
) -> CandidateAssessment | None:
    """Score a candidate against a role (§6.8).

    Args:
        candidate: the serving candidate to score.
        scorecard: the clarified role requirements.
        lm: optional DSPy LM (PseudonymisedLM). None → stub sub-scores.
        adjacency_map: skill-name → adjacent-skill-names for partial credit (AD-035).
        weights: ``{"skill": float, "feedback": float}`` (default 0.7/0.3, AD-030).
        freshness_verdict: if ``warn``, every assessment gets a FRESHNESS_WARNING flag.

    Returns:
        ``CandidateAssessment`` on success, ``None`` on LLM error (caller filters).
    """
    adj_map = adjacency_map or {}
    w = weights or {"skill": 0.7, "feedback": 0.3}
    w_skill = w.get("skill", 0.7)
    w_feedback = w.get("feedback", 0.3)

    hard_cov = _hard_skill_coverage(candidate, scorecard.hard_depth_skills)
    desired_cov, adjacency_used = _desired_skill_coverage(
        candidate,
        scorecard.desired_skills,
        adj_map,
    )

    source_text = _build_source_text(candidate)

    if lm is not None:
        sig = ScoreSignature.with_instructions(load_prompt("score_candidate"))
        predictor = dspy.Predict(sig)

        role_text = "; ".join(
            f"{s.name} ({s.depth.value})"
            for s in scorecard.hard_depth_skills + scorecard.desired_skills
        )
        feedback_text = (
            " | ".join(entry.text for entry in candidate.feedback.entries) or "(no feedback)"
        )

        try:
            with dspy.context(lm=lm):
                result = predictor(
                    role_requirements=role_text,
                    candidate_profile=candidate.profile_summary or "(no profile)",
                    candidate_feedback=feedback_text,
                )
            skill_match = _clamp(float(result.skill_match_score))
            feedback_score = _clamp(float(result.feedback_score))
            narrative = str(result.narrative).strip()
            evidence = _verify_citations(result.evidence, source_text)
        except Exception as exc:
            _log.warning(
                "score.llm_failed",
                candidate_email=candidate.email,
                reason=type(exc).__name__,
            )
            return None
    else:
        skill_match = hard_cov
        feedback_score = 0.0
        narrative = f"Stub assessment for {candidate.email}."
        evidence = []

    combined = w_skill * skill_match + w_feedback * feedback_score
    flags = _build_flags(candidate, adjacency_used, freshness_verdict)

    return CandidateAssessment(
        candidate=candidate,
        skill_match_score=skill_match,
        feedback_score=feedback_score,
        combined_score=combined,
        flags=flags,
        evidence=evidence,
        narrative=narrative,
        hard_skill_coverage=hard_cov,
        desired_skill_coverage=desired_cov,
    )
