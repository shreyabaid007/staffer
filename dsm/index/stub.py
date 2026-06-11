"""Stub retrieval for end-to-end testing (Slice 0)."""

from dsm.models import Candidate, EligiblePool, TargetProfileScorecard


def retrieve_candidates(
    pool: EligiblePool,
    scorecard: TargetProfileScorecard,
    top_k: int = 10,
) -> list[Candidate]:
    """Stub: return first N candidates from the pool."""
    return pool.candidates[:top_k]
