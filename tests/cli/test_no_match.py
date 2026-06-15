"""Orchestrator no-match path tests (O-NM-1..5, E-R03)."""

from __future__ import annotations

import json

import pytest

import dsm.cli.commands as commands
from dsm.cli.commands import build_near_misses, run_match
from dsm.match.gates import filter_candidates
from dsm.models import ExclusionReason, NoMatchResult
from tests.fixtures import role_03


def test_e_r03_role_03_returns_no_match_with_ordered_near_misses() -> None:
    """E-R03 / O-NM-1/2/3: ROLE-03 → NoMatchResult, near-misses [Sanjay, Meera, Arjun]."""
    candidates, scorecard = role_03()
    result = run_match(candidates, scorecard)

    assert isinstance(result, NoMatchResult)
    assert result.role_id == "ROLE-03"
    assert result.reason  # human-readable, non-empty (O-NM-1)
    assert [nm.candidate_email for nm in result.near_misses] == [
        "sanjay@example.com",
        "meera@example.com",
        "arjun@example.com",
    ]


def test_o_nm_3_near_misses_capped_at_three() -> None:
    """O-NM-3: all four ROLE-03 candidates fail, but only three near-misses surface."""
    candidates, scorecard = role_03()
    result = run_match(candidates, scorecard)
    assert isinstance(result, NoMatchResult)
    assert len(result.near_misses) == 3
    # Kavita (the second, later-alphabetical location miss) is the one dropped by the cap.
    assert "kavita@example.com" not in [nm.candidate_email for nm in result.near_misses]


def test_o_nm_2_full_order_availability_before_location() -> None:
    """O-NM-2: availability misses (smallest overshoot first) precede location misses.

    build_near_misses returns the full ordered list (the cap is applied by the
    orchestrator), so all four ROLE-03 misses appear in AD-063(b) order here.
    """
    candidates, scorecard = role_03()
    _, exclusion_log = filter_candidates(candidates, scorecard)
    near_misses = build_near_misses(candidates, scorecard, exclusion_log)

    assert [nm.candidate_email for nm in near_misses] == [
        "sanjay@example.com",  # availability, +1d
        "meera@example.com",  # availability, +31d
        "arjun@example.com",  # location, email < kavita
        "kavita@example.com",  # location
    ]
    assert [nm.reason for nm in near_misses] == [
        ExclusionReason.AVAILABILITY_MISMATCH.value,
        ExclusionReason.AVAILABILITY_MISMATCH.value,
        ExclusionReason.LOCATION_MISMATCH.value,
        ExclusionReason.LOCATION_MISMATCH.value,
    ]


def test_o_nm_4_gap_summaries_recomputed_and_human_readable() -> None:
    """O-NM-4: gap summaries reflect overshoots computed from structured data."""
    candidates, scorecard = role_03()
    _, exclusion_log = filter_candidates(candidates, scorecard)
    near_misses = build_near_misses(candidates, scorecard, exclusion_log)
    summaries = {nm.candidate_email: nm.gap_summary for nm in near_misses}

    assert summaries["sanjay@example.com"] == "available 1 day after deadline"
    assert summaries["meera@example.com"] == "available 31 days after deadline"
    assert summaries["arjun@example.com"] == "in Pune, not open to relocation"


def test_o_nm_1_rank_not_called_when_pool_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O-NM-1: the orchestrator builds NoMatchResult and never invokes rank for an empty pool."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("rank_assessments must not be called when the pool is empty")

    monkeypatch.setattr(commands, "rank_assessments", _boom)
    candidates, scorecard = role_03()
    result = run_match(candidates, scorecard)
    assert isinstance(result, NoMatchResult)


def test_o_nm_5_no_match_result_renders_to_json() -> None:
    """O-NM-5: the NoMatchResult (reason + near-misses + gap summaries) serialises for the CLI."""
    candidates, scorecard = role_03()
    result = run_match(candidates, scorecard)
    payload = json.loads(result.model_dump_json())

    assert payload["reason"]
    assert len(payload["near_misses"]) == 3
    assert payload["near_misses"][0]["candidate_email"] == "sanjay@example.com"
    assert payload["near_misses"][0]["gap_summary"] == "available 1 day after deadline"
