"""Candidate scoring via DSPy — produces CandidateAssessment."""

from dsm.models import Candidate, CandidateAssessment, TargetProfileScorecard


def score_candidate(candidate: Candidate, scorecard: TargetProfileScorecard) -> CandidateAssessment:
    """Stub: fixed scores."""
    return CandidateAssessment(
        candidate=candidate,
        skill_match_score=0.75,
        feedback_score=0.6,
        combined_score=0.7 * 0.75 + 0.3 * 0.6,  # 0.705
        flags=[],
        evidence=[],
        narrative=f"Stub assessment for {candidate.name}",
        hard_skill_coverage=0.8,
        desired_skill_coverage=0.7,
    )
