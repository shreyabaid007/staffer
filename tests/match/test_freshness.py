"""Tests for the query-time freshness guard (B-1 T-008; FR-2; AD-087).

Covers all four decision-tree branches plus the staleness boundary. Pure datetime
arithmetic — deterministic, no fixtures beyond the dates.
"""

from __future__ import annotations

from datetime import date

from dsm.match.freshness import OK, REFUSE, WARN, check_freshness

_MAX = 30
_START = date(2026, 7, 1)


def test_fr_2_ac_1_supply_fresher_than_demand_is_ok() -> None:
    """FR-2-AC-1: supply fresher than demand (staleness ≤ 0) → ok."""
    verdict = check_freshness(
        demand_as_of=date(2026, 6, 1),
        valid_as_of=date(2026, 6, 10),  # supply newer than demand
        start_date=_START,
        max_staleness_days=_MAX,
    )
    assert verdict.action == OK
    assert verdict.staleness_days == -9


def test_staleness_zero_is_ok() -> None:
    """Same-day demand and supply → staleness 0 → ok."""
    verdict = check_freshness(
        demand_as_of=date(2026, 6, 10),
        valid_as_of=date(2026, 6, 10),
        start_date=_START,
        max_staleness_days=_MAX,
    )
    assert verdict.action == OK
    assert verdict.staleness_days == 0


def test_fr_2_ac_2_staleness_over_max_refuses() -> None:
    """FR-2-AC-2: staleness > max → refuse."""
    verdict = check_freshness(
        demand_as_of=date(2026, 7, 20),
        valid_as_of=date(2026, 6, 1),  # 49 days stale
        start_date=_START,
        max_staleness_days=_MAX,
    )
    assert verdict.action == REFUSE
    assert verdict.staleness_days == 49


def test_fr_2_ac_3_within_max_and_start_before_valid_warns() -> None:
    """FR-2-AC-3: 0 < staleness ≤ max AND start_date < valid_as_of → warn."""
    verdict = check_freshness(
        demand_as_of=date(2026, 6, 20),
        valid_as_of=date(2026, 6, 10),  # 10 days stale (within 30)
        start_date=date(2026, 6, 5),  # role start precedes the snapshot
        max_staleness_days=_MAX,
    )
    assert verdict.action == WARN
    assert verdict.staleness_days == 10


def test_fr_2_ac_4_within_max_and_start_after_valid_is_ok() -> None:
    """FR-2-AC-4: 0 < staleness ≤ max AND start_date ≥ valid_as_of → ok."""
    verdict = check_freshness(
        demand_as_of=date(2026, 6, 20),
        valid_as_of=date(2026, 6, 10),  # 10 days stale (within 30)
        start_date=date(2026, 7, 1),  # role start is after the snapshot
        max_staleness_days=_MAX,
    )
    assert verdict.action == OK
    assert verdict.staleness_days == 10


def test_start_equal_to_valid_as_of_is_ok_not_warn() -> None:
    """Boundary: start_date == valid_as_of is not 'before' → ok (warn needs strict <)."""
    verdict = check_freshness(
        demand_as_of=date(2026, 6, 20),
        valid_as_of=date(2026, 6, 10),
        start_date=date(2026, 6, 10),  # equal, not before
        max_staleness_days=_MAX,
    )
    assert verdict.action == OK


def test_staleness_exactly_at_max_is_ok() -> None:
    """Boundary: staleness == max → ok (≤ is inclusive); start_date after snapshot."""
    verdict = check_freshness(
        demand_as_of=date(2026, 7, 10),
        valid_as_of=date(2026, 6, 10),  # exactly 30 days
        start_date=date(2026, 8, 1),
        max_staleness_days=_MAX,
    )
    assert verdict.action == OK
    assert verdict.staleness_days == 30


def test_staleness_one_past_max_refuses() -> None:
    """Boundary: staleness == max + 1 → refuse."""
    verdict = check_freshness(
        demand_as_of=date(2026, 7, 11),
        valid_as_of=date(2026, 6, 10),  # 31 days
        start_date=date(2026, 8, 1),
        max_staleness_days=_MAX,
    )
    assert verdict.action == REFUSE
    assert verdict.staleness_days == 31


def test_refuse_wins_even_when_start_before_valid() -> None:
    """Refuse is checked first: staleness > max blocks regardless of the start_date branch."""
    verdict = check_freshness(
        demand_as_of=date(2026, 8, 1),
        valid_as_of=date(2026, 6, 1),  # 61 days stale
        start_date=date(2026, 5, 1),  # would otherwise be the warn branch
        max_staleness_days=_MAX,
    )
    assert verdict.action == REFUSE
