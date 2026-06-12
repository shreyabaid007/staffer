"""Unit tests for the stub gates module."""

from dsm.match.gates import filter_candidates
from dsm.models import Candidate, TargetProfileScorecard


def test_stub_allows_all(
    sample_candidates: list[Candidate], sample_scorecard: TargetProfileScorecard
) -> None:
    """Slice 0 stub: all candidates pass gates."""
    pool, log = filter_candidates(sample_candidates, sample_scorecard)
    assert len(pool.candidates) == len(sample_candidates)
    assert len(log.exclusions) == 0
