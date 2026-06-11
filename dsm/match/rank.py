"""Deterministic ranking — weighted combination of sub-scores."""

from typing import Any

from dsm.models import CandidateAssessment, ExclusionLog, ShortlistResult


def rank_assessments(
    assessments: list[CandidateAssessment],
    role_id: str,
    exclusion_log: ExclusionLog,
    top_k: int = 5,
) -> ShortlistResult:
    """Stub: sort by combined_score desc, take top K."""
    ranked = sorted(assessments, key=lambda a: a.combined_score, reverse=True)[:top_k]
    config: dict[str, Any] = {"top_k": top_k, "weights": {"skill": 0.7, "feedback": 0.3}}
    return ShortlistResult(
        role_id=role_id,
        ranked_assessments=ranked,
        total_eligible=len(assessments),
        exclusion_log=exclusion_log,
        config_snapshot=config,
    )
