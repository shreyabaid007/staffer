"""Unit tests for deterministic ranking (R-SORT-1, R-TIE-1, R-TOP-1, R-OUT-1)."""

from __future__ import annotations

from dsm.match.rank import rank_assessments
from dsm.models import (
    Candidate,
    CandidateAssessment,
    CandidateSource,
    ExclusionLog,
    FeedbackSignals,
    FreeNow,
    Location,
    ProficiencyLevel,
    Skill,
)

_EMPTY_LOG = ExclusionLog(exclusions=[])
_SNAPSHOT = {"top_k": 5, "weights": {"skill": 0.7, "feedback": 0.3}, "models": {}}


def _assessment(
    *,
    email: str,
    combined: float,
    hard: float = 0.5,
    desired: float = 0.5,
) -> CandidateAssessment:
    """Build an assessment with the score fields ranking sorts on; rest are filler."""
    candidate = Candidate(
        email=email,
        name="Test Candidate",
        location=Location(city="Chennai"),
        availability=FreeNow(),
        skills=[Skill(name="python", proficiency=ProficiencyLevel.ADVANCED)],
        feedback=FeedbackSignals(),
        source=CandidateSource.BEACH,
    )
    return CandidateAssessment(
        candidate=candidate,
        skill_match_score=combined,
        feedback_score=combined,
        combined_score=combined,
        flags=[],
        evidence=[],
        narrative="test",
        hard_skill_coverage=hard,
        desired_skill_coverage=desired,
    )


def _rank(assessments: list[CandidateAssessment], top_k: int = 5):
    """Rank with a fixed role/log/snapshot so tests vary only the assessments + top_k."""
    return rank_assessments(assessments, "ROLE-TEST", _EMPTY_LOG, top_k, _SNAPSHOT)


def _emails(result) -> list[str]:
    return [a.candidate.email for a in result.ranked_assessments]


# ---------------------------------------------------------------------------
# R-SORT-1 — combined_score descending
# ---------------------------------------------------------------------------


def test_r_sort_1_sorts_by_combined_score_desc() -> None:
    """R-SORT-1: higher combined_score ranks first."""
    result = _rank(
        [
            _assessment(email="low@example.com", combined=0.30),
            _assessment(email="high@example.com", combined=0.90),
            _assessment(email="mid@example.com", combined=0.60),
        ]
    )
    assert _emails(result) == ["high@example.com", "mid@example.com", "low@example.com"]


# ---------------------------------------------------------------------------
# R-TIE-1 — tie-break: hard desc → desired desc → email asc
# ---------------------------------------------------------------------------


def test_r_tie_1_breaks_on_hard_skill_coverage() -> None:
    """R-TIE-1: equal combined_score → higher hard_skill_coverage first."""
    result = _rank(
        [
            _assessment(email="a@example.com", combined=0.70, hard=0.2),
            _assessment(email="b@example.com", combined=0.70, hard=0.9),
        ]
    )
    assert _emails(result) == ["b@example.com", "a@example.com"]


def test_r_tie_1_breaks_on_desired_when_hard_equal() -> None:
    """R-TIE-1: equal combined + hard → higher desired_skill_coverage first."""
    result = _rank(
        [
            _assessment(email="a@example.com", combined=0.70, hard=0.5, desired=0.1),
            _assessment(email="b@example.com", combined=0.70, hard=0.5, desired=0.8),
        ]
    )
    assert _emails(result) == ["b@example.com", "a@example.com"]


def test_r_tie_1_breaks_on_email_when_all_scores_equal() -> None:
    """R-TIE-1: identical scores → email ascending (the deterministic final tie-break)."""
    result = _rank(
        [
            _assessment(email="charlie@example.com", combined=0.70, hard=0.5, desired=0.5),
            _assessment(email="alice@example.com", combined=0.70, hard=0.5, desired=0.5),
            _assessment(email="bob@example.com", combined=0.70, hard=0.5, desired=0.5),
        ]
    )
    assert _emails(result) == [
        "alice@example.com",
        "bob@example.com",
        "charlie@example.com",
    ]


# ---------------------------------------------------------------------------
# R-TOP-1 — top-k truncation
# ---------------------------------------------------------------------------


def test_r_top_1_truncates_to_top_k() -> None:
    """R-TOP-1: 6 candidates, top_k=5 → exactly the 5 highest are returned."""
    assessments = [_assessment(email=f"c{i}@example.com", combined=float(i)) for i in range(6)]
    result = _rank(assessments, top_k=5)
    assert len(result.ranked_assessments) == 5
    # The lowest score (c0, combined=0.0) is dropped.
    assert "c0@example.com" not in _emails(result)
    assert result.total_eligible == 6


def test_fewer_than_top_k_returns_all() -> None:
    """Edge case: fewer eligible than top_k → return all, no padding."""
    result = _rank(
        [
            _assessment(email="a@example.com", combined=0.5),
            _assessment(email="b@example.com", combined=0.4),
        ],
        top_k=5,
    )
    assert len(result.ranked_assessments) == 2


# ---------------------------------------------------------------------------
# R-OUT-1 — empty input
# ---------------------------------------------------------------------------


def test_r_out_1_empty_input_returns_empty_shortlist() -> None:
    """R-OUT-1: empty assessments → empty ShortlistResult, not a NoMatchResult."""
    result = _rank([], top_k=5)
    assert result.ranked_assessments == []
    assert result.total_eligible == 0
    assert result.role_id == "ROLE-TEST"


# ---------------------------------------------------------------------------
# Determinism + passthrough
# ---------------------------------------------------------------------------


def test_determinism_same_input_twice() -> None:
    """Determinism: identical input (even when pre-scrambled) yields identical output."""
    scrambled = [
        _assessment(email="d@example.com", combined=0.70, hard=0.5, desired=0.5),
        _assessment(email="a@example.com", combined=0.70, hard=0.5, desired=0.5),
        _assessment(email="c@example.com", combined=0.70, hard=0.5, desired=0.5),
        _assessment(email="b@example.com", combined=0.70, hard=0.5, desired=0.5),
    ]
    first = _rank(list(scrambled))
    second = _rank(list(scrambled))
    assert _emails(first) == _emails(second)
    assert _emails(first) == [
        "a@example.com",
        "b@example.com",
        "c@example.com",
        "d@example.com",
    ]


def test_passes_through_log_and_snapshot() -> None:
    """The exclusion log and config snapshot are carried onto the result verbatim."""
    log = ExclusionLog(exclusions=[])
    result = rank_assessments([], "ROLE-X", log, 5, _SNAPSHOT)
    assert result.exclusion_log is log
    assert result.config_snapshot == _SNAPSHOT
