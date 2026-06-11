"""Deterministic eligibility gates — pure Python, no LLM imports."""

from dsm.models import Candidate, EligiblePool, ExclusionLog, TargetProfileScorecard


def filter_candidates(
    candidates: list[Candidate],
    scorecard: TargetProfileScorecard,
) -> tuple[EligiblePool, ExclusionLog]:
    """Stub: all candidates pass."""
    return (
        EligiblePool(candidates=candidates, scorecard_id=scorecard.role_id),
        ExclusionLog(exclusions=[]),
    )
