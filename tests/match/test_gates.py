"""Unit tests for the deterministic eligibility gates.

Covers the location gate (G-LOC-1..4), the output contract (G-OUT-1, G-OUT-2),
and the availability gate (G-AVL-1..6). All gate behaviour is pure Python over the
frozen models — no LLM, no network.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

import pytest

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
    NewJoiner,
    ProficiencyLevel,
    RollingOff,
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


# ---------------------------------------------------------------------------
# Availability gate — G-AVL-1..6, G-OUT-2
# (co-location not required, so location is a non-factor and availability is isolated)
# ---------------------------------------------------------------------------

# Deadline for these tests: 2026-07-01 + 14d = 2026-07-15.
_DEADLINE = date(2026, 7, 15)


def test_g_avl_1_free_now_always_included() -> None:
    """G-AVL-1: FreeNow passes regardless of the role start date."""
    scorecard = _scorecard(co_location_required=False)
    pool, log = filter_candidates([_candidate(availability=FreeNow())], scorecard)
    assert len(pool.candidates) == 1
    assert log.exclusions == []


def test_g_avl_2_rolling_off_within_window_included() -> None:
    """G-AVL-2: RollingOff with expected_date <= deadline → included."""
    scorecard = _scorecard(co_location_required=False)
    candidate = _candidate(
        availability=RollingOff(expected_date=date(2026, 7, 10), confidence="high")
    )
    pool, log = filter_candidates([candidate], scorecard)
    assert len(pool.candidates) == 1
    assert log.exclusions == []


def test_g_avl_3_rolling_off_past_window_excluded_with_both_dates() -> None:
    """G-AVL-3: RollingOff past deadline → excluded; detail has free-date and deadline."""
    scorecard = _scorecard(co_location_required=False)
    candidate = _candidate(
        availability=RollingOff(expected_date=date(2026, 8, 1), confidence="high")
    )
    pool, log = filter_candidates([candidate], scorecard)
    assert pool.candidates == []
    assert len(log.exclusions) == 1
    exclusion = log.exclusions[0]
    assert exclusion.reason is ExclusionReason.AVAILABILITY_MISMATCH
    assert "2026-08-01" in exclusion.detail
    assert "2026-07-15" in exclusion.detail


def test_g_avl_4_new_joiner_within_window_included() -> None:
    """G-AVL-4: NewJoiner with join_date <= deadline → included."""
    scorecard = _scorecard(co_location_required=False)
    candidate = _candidate(availability=NewJoiner(join_date=date(2026, 7, 14)))
    pool, log = filter_candidates([candidate], scorecard)
    assert len(pool.candidates) == 1
    assert log.exclusions == []


def test_g_avl_5_new_joiner_past_window_excluded_with_both_dates() -> None:
    """G-AVL-5: NewJoiner past deadline → excluded; detail has join_date and deadline."""
    scorecard = _scorecard(co_location_required=False)
    candidate = _candidate(availability=NewJoiner(join_date=date(2026, 8, 15)))
    pool, log = filter_candidates([candidate], scorecard)
    assert pool.candidates == []
    assert len(log.exclusions) == 1
    exclusion = log.exclusions[0]
    assert exclusion.reason is ExclusionReason.AVAILABILITY_MISMATCH
    assert "2026-08-15" in exclusion.detail
    assert "2026-07-15" in exclusion.detail


@pytest.mark.parametrize("confidence", ["high", "medium", "low"])
def test_g_avl_6_confidence_does_not_affect_gating(
    confidence: Literal["high", "medium", "low"],
) -> None:
    """G-AVL-6: RollingOff gates on expected_date identically at every confidence (AD-022).

    Within the window passes and past it fails regardless of confidence — low confidence
    is a downstream ROLL_OFF_UNCERTAIN flag, never a gate.
    """
    scorecard = _scorecard(co_location_required=False)
    within = _candidate(
        availability=RollingOff(expected_date=date(2026, 7, 10), confidence=confidence)
    )
    past = _candidate(
        availability=RollingOff(expected_date=date(2026, 8, 1), confidence=confidence)
    )
    within_pool, within_log = filter_candidates([within], scorecard)
    past_pool, past_log = filter_candidates([past], scorecard)
    assert len(within_pool.candidates) == 1 and within_log.exclusions == []
    assert past_pool.candidates == [] and len(past_log.exclusions) == 1


def test_availability_boundary_exactly_on_deadline_passes() -> None:
    """Edge case: free exactly on the deadline day (+14d) → passes (<=, not <)."""
    scorecard = _scorecard(co_location_required=False, window_days=14)
    candidate = _candidate(availability=RollingOff(expected_date=_DEADLINE, confidence="high"))
    pool, log = filter_candidates([candidate], scorecard)
    assert len(pool.candidates) == 1
    assert log.exclusions == []


def test_availability_boundary_one_day_past_deadline_excluded() -> None:
    """Edge case: free one day after the deadline (+15d) → excluded."""
    scorecard = _scorecard(co_location_required=False, window_days=14)
    candidate = _candidate(
        availability=RollingOff(expected_date=date(2026, 7, 16), confidence="high")
    )
    pool, log = filter_candidates([candidate], scorecard)
    assert pool.candidates == []
    assert len(log.exclusions) == 1
    assert log.exclusions[0].reason is ExclusionReason.AVAILABILITY_MISMATCH


def test_g_out_2_both_gates_fail_records_only_location() -> None:
    """G-OUT-2: when location and availability both fail, only LOCATION_MISMATCH is recorded."""
    scorecard = _scorecard(city="Chennai", co_location_required=True)
    candidate = _candidate(
        city="Pune",
        remote_eligible=False,
        availability=RollingOff(expected_date=date(2026, 8, 1), confidence="high"),
    )
    pool, log = filter_candidates([candidate], scorecard)
    assert pool.candidates == []
    assert len(log.exclusions) == 1
    assert log.exclusions[0].reason is ExclusionReason.LOCATION_MISMATCH


def test_empty_candidate_list_returns_empty_pool_and_log() -> None:
    """Edge case (design.md): empty input → empty EligiblePool + empty ExclusionLog."""
    scorecard = _scorecard(co_location_required=True)
    pool, log = filter_candidates([], scorecard)
    assert pool.candidates == []
    assert log.exclusions == []
    assert pool.scorecard_id == "ROLE-TEST"
