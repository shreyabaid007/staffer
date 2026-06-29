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
    city: str | None = "Chennai",
    country: str = "India",
    co_location_required: bool,
    window_days: int = 14,
    exclude_cities: frozenset[str] = frozenset(),
) -> TargetProfileScorecard:
    """Build a minimal scorecard; skills are irrelevant to the gates."""
    return TargetProfileScorecard(
        role_id="ROLE-TEST",
        hard_depth_skills=[SkillRequirement(name="python", depth=SkillDepth.HARD)],
        desired_skills=[],
        location=Location(city=city, country=country),
        co_location_required=co_location_required,
        exclude_cities=exclude_cities,
        start_date=_START,
        availability_window_days=window_days,
    )


def _candidate(
    *,
    email: str = "c@example.com",
    city: str | None = "Chennai",
    country: str = "India",
    remote_within_country: bool = False,
    onsite_cities: frozenset[str] = frozenset(),
    availability: AvailabilityState | None = None,
) -> Candidate:
    """Build a candidate; defaults to FreeNow so location tests isolate location."""
    return Candidate(
        email=email,
        name="Test Candidate",
        location=Location(
            city=city,
            country=country,
            remote_within_country=remote_within_country,
            onsite_cities=onsite_cities,
        ),
        availability=availability or FreeNow(),
        skills=[Skill(name="python", proficiency=ProficiencyLevel.ADVANCED)],
        feedback=FeedbackSignals(),
        source=CandidateSource.BEACH,
    )


# ---------------------------------------------------------------------------
# Query-side negation — c-007 exclude_cities (FR-3-AC-1..6)
# ---------------------------------------------------------------------------


def test_c007_excluded_home_city_excluded_onsite() -> None:
    """FR-3-AC-1: excluded home city → LOCATION_MISMATCH (onsite role) + exclusion detail."""
    scorecard = _scorecard(
        city="Bengaluru", co_location_required=True, exclude_cities=frozenset({"chennai"})
    )
    pool, log = filter_candidates([_candidate(city="Chennai")], scorecard)
    assert pool.candidates == []
    assert log.exclusions[0].reason is ExclusionReason.LOCATION_MISMATCH
    assert "excludes" in log.exclusions[0].detail  # FR-3-AC-6 exclusion-specific wording


def test_c007_excluded_home_city_excluded_distributed() -> None:
    """FR-3-AC-1: the exclusion fires even for a distributed (co_location_required=False) role."""
    scorecard = _scorecard(
        city=None, co_location_required=False, exclude_cities=frozenset({"Chennai"})
    )
    pool, log = filter_candidates([_candidate(city="Chennai")], scorecard)  # case-insensitive
    assert pool.candidates == []
    assert log.exclusions[0].reason is ExclusionReason.LOCATION_MISMATCH


def test_c007_non_excluded_city_passes_distributed() -> None:
    """A Pune candidate clears a distributed 'not Chennai' role."""
    scorecard = _scorecard(
        city=None, co_location_required=False, exclude_cities=frozenset({"chennai"})
    )
    pool, log = filter_candidates([_candidate(city="Pune")], scorecard)
    assert len(pool.candidates) == 1 and log.exclusions == []


def test_c007_onsite_cities_willingness_not_triggered() -> None:
    """FR-3-AC-2: exclusion matches home city only — onsite_cities willingness does not trigger."""
    # Candidate is in Pune (not excluded) but open to onsite in Chennai (excluded). Home city wins.
    scorecard = _scorecard(
        city=None, co_location_required=False, exclude_cities=frozenset({"chennai"})
    )
    candidate = _candidate(city="Pune", onsite_cities=frozenset({"Chennai"}))
    pool, _ = filter_candidates([candidate], scorecard)
    assert len(pool.candidates) == 1  # not excluded — home city is Pune


def test_c007_empty_exclude_cities_is_unchanged() -> None:
    """FR-3-AC-3: the default empty set leaves the gate byte-identical (Chennai onsite passes)."""
    scorecard = _scorecard(
        city="Chennai", co_location_required=True
    )  # exclude_cities defaults empty
    pool, log = filter_candidates([_candidate(city="Chennai")], scorecard)
    assert len(pool.candidates) == 1 and log.exclusions == []


# ---------------------------------------------------------------------------
# Location gate — AD-086 (onsite vs distributed), FR-3-AC-1..7, G-OUT-1
# ---------------------------------------------------------------------------


def test_g_loc_1_onsite_city_match_included() -> None:
    """FR-3: onsite required + home-city match → included."""
    scorecard = _scorecard(city="Chennai", co_location_required=True)
    pool, log = filter_candidates([_candidate(city="Chennai")], scorecard)
    assert len(pool.candidates) == 1
    assert log.exclusions == []


def test_g_loc_2_onsite_cities_membership_included() -> None:
    """FR-3-AC-2: onsite required + role city in candidate.onsite_cities → included (AD-086)."""
    scorecard = _scorecard(city="Chennai", co_location_required=True)
    candidate = _candidate(city="Pune", onsite_cities=frozenset({"Chennai"}))
    pool, log = filter_candidates([candidate], scorecard)
    assert len(pool.candidates) == 1
    assert log.exclusions == []


def test_g_loc_3_remote_within_country_does_not_clear_onsite() -> None:
    """FR-3-AC-3: onsite required + remote_within_country (not onsite-for-city) → excluded."""
    scorecard = _scorecard(city="Chennai", co_location_required=True)
    candidate = _candidate(city="Pune", remote_within_country=True)
    pool, log = filter_candidates([candidate], scorecard)
    assert pool.candidates == []
    assert len(log.exclusions) == 1
    exclusion = log.exclusions[0]
    assert exclusion.reason is ExclusionReason.LOCATION_MISMATCH
    assert exclusion.candidate_email == "c@example.com"
    assert "Pune" in exclusion.detail
    assert "Chennai" in exclusion.detail


def test_g_loc_role_city_none_excludes_all_onsite() -> None:
    """FR-3-AC-1: onsite required but role has no city → no candidate can match → excluded."""
    scorecard = _scorecard(city=None, co_location_required=True)
    candidate = _candidate(city="Chennai", onsite_cities=frozenset({"Chennai"}))
    pool, log = filter_candidates([candidate], scorecard)
    assert pool.candidates == []
    assert len(log.exclusions) == 1
    assert log.exclusions[0].reason is ExclusionReason.LOCATION_MISMATCH


def test_g_loc_distributed_same_country_passes() -> None:
    """FR-3-AC-5: distributed role → same-country candidates pass regardless of city."""
    scorecard = _scorecard(city="Chennai", country="India", co_location_required=False)
    candidates = [
        _candidate(email="a@example.com", city="Pune"),
        _candidate(email="b@example.com", city="Bangalore"),
        _candidate(email="d@example.com", city=None, remote_within_country=True),
    ]
    pool, log = filter_candidates(candidates, scorecard)
    assert len(pool.candidates) == 3
    assert log.exclusions == []


def test_g_loc_distributed_different_country_excluded() -> None:
    """FR-3-AC-5: distributed role → a different-country candidate is excluded."""
    scorecard = _scorecard(city="Chennai", country="India", co_location_required=False)
    candidate = _candidate(city="London", country="UK")
    pool, log = filter_candidates([candidate], scorecard)
    assert pool.candidates == []
    assert len(log.exclusions) == 1
    assert log.exclusions[0].reason is ExclusionReason.LOCATION_MISMATCH


def test_location_home_city_match_is_case_insensitive() -> None:
    """FR-3-AC-4: 'chennai' matches 'Chennai' (casefold, stripped)."""
    scorecard = _scorecard(city="Chennai", co_location_required=True)
    pool, log = filter_candidates([_candidate(city="  chennai ")], scorecard)
    assert len(pool.candidates) == 1
    assert log.exclusions == []


def test_location_onsite_membership_is_case_insensitive() -> None:
    """FR-3-AC-4: onsite-city membership matches case-insensitively too (AD-086)."""
    scorecard = _scorecard(city="Chennai", co_location_required=True)
    candidate = _candidate(city="Pune", onsite_cities=frozenset({"chennai"}))
    pool, log = filter_candidates([candidate], scorecard)
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
