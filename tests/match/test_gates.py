"""Unit tests for the deterministic eligibility gates.

Covers the location gate (G-LOC-1..4), the output contract (G-OUT-1, G-OUT-2),
and the availability gate (G-AVL-1..6). All gate behaviour is pure Python over the
frozen models — no LLM, no network.
"""

from __future__ import annotations

from datetime import date

from dsm.match.gates import filter_candidates
from dsm.models import (
    AvailabilityState,
    Candidate,
    CandidateSource,
    EligiblePool,
    ExclusionLog,
    ExclusionReason,
    FeedbackSignals,
    FreeNow,
    Location,
    ProficiencyLevel,
    Skill,
    SkillDepth,
    SkillRequirement,
    TargetProfileScorecard,
)

_START = date(2026, 7, 1)


def _scorecard(
    *,
    city: str = "Chennai",
    co_location_required: bool,
    window_days: int = 14,
) -> TargetProfileScorecard:
    """Build a minimal scorecard; skills are irrelevant to the gates."""
    return TargetProfileScorecard(
        role_id="ROLE-TEST",
        hard_depth_skills=[SkillRequirement(name="python", depth=SkillDepth.HARD)],
        desired_skills=[],
        location=Location(city=city),
        co_location_required=co_location_required,
        start_date=_START,
        availability_window_days=window_days,
    )


def _candidate(
    *,
    email: str = "c@example.com",
    city: str = "Chennai",
    remote_eligible: bool = False,
    availability: AvailabilityState | None = None,
) -> Candidate:
    """Build a candidate; defaults to FreeNow so location tests isolate location."""
    return Candidate(
        email=email,
        name="Test Candidate",
        location=Location(city=city, remote_eligible=remote_eligible),
        availability=availability or FreeNow(),
        skills=[Skill(name="python", proficiency=ProficiencyLevel.ADVANCED)],
        feedback=FeedbackSignals(),
        source=CandidateSource.BEACH,
    )


# ---------------------------------------------------------------------------
# Location gate — G-LOC-1..4, G-OUT-1
# ---------------------------------------------------------------------------


def test_g_loc_1_co_location_city_match_included() -> None:
    """G-LOC-1: co-location required + city match → included."""
    scorecard = _scorecard(city="Chennai", co_location_required=True)
    pool, log = filter_candidates([_candidate(city="Chennai")], scorecard)
    assert len(pool.candidates) == 1
    assert log.exclusions == []


def test_g_loc_2_co_location_remote_eligible_included() -> None:
    """G-LOC-2: co-location required + different city + remote_eligible → included (AD-063a)."""
    scorecard = _scorecard(city="Chennai", co_location_required=True)
    candidate = _candidate(city="Pune", remote_eligible=True)
    pool, log = filter_candidates([candidate], scorecard)
    assert len(pool.candidates) == 1
    assert log.exclusions == []


def test_g_loc_3_co_location_mismatch_excluded_with_both_cities() -> None:
    """G-LOC-3: co-location + different city + not remote → excluded, both cities in detail."""
    scorecard = _scorecard(city="Chennai", co_location_required=True)
    candidate = _candidate(city="Pune", remote_eligible=False)
    pool, log = filter_candidates([candidate], scorecard)
    assert pool.candidates == []
    assert len(log.exclusions) == 1
    exclusion = log.exclusions[0]
    assert exclusion.reason is ExclusionReason.LOCATION_MISMATCH
    assert exclusion.candidate_email == "c@example.com"
    assert "Pune" in exclusion.detail
    assert "Chennai" in exclusion.detail


def test_g_loc_4_no_co_location_all_pass() -> None:
    """G-LOC-4: co-location not required → any India city passes."""
    scorecard = _scorecard(city="Chennai", co_location_required=False)
    candidates = [
        _candidate(email="a@example.com", city="Pune"),
        _candidate(email="b@example.com", city="Bangalore"),
        _candidate(email="d@example.com", city="Kolkata"),
    ]
    pool, log = filter_candidates(candidates, scorecard)
    assert len(pool.candidates) == 3
    assert log.exclusions == []


def test_location_city_match_is_case_insensitive() -> None:
    """Edge case: 'chennai' matches 'Chennai' (normalise to lowercase, stripped)."""
    scorecard = _scorecard(city="Chennai", co_location_required=True)
    pool, log = filter_candidates([_candidate(city="  chennai ")], scorecard)
    assert len(pool.candidates) == 1
    assert log.exclusions == []


def test_g_out_1_returns_typed_pool_and_log() -> None:
    """G-OUT-1: gates return (EligiblePool, ExclusionLog) with scorecard_id set."""
    scorecard = _scorecard(co_location_required=False)
    pool, log = filter_candidates([_candidate()], scorecard)
    assert isinstance(pool, EligiblePool)
    assert isinstance(log, ExclusionLog)
    assert pool.scorecard_id == "ROLE-TEST"
