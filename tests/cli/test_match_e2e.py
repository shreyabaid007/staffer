"""Integration tests: drive the orchestrator with injected fixtures (E-R01, E-R02, E-R03).

These exercise the real gates → score → rank (or gates → no-match) flow via ``run_match``
with fixture inputs — they do NOT wire ROLE-01/02/03 into the production ``dsm match`` CLI,
which keeps using whatever ingest provides. A thin subprocess smoke test covers the actual
command entry point over the stub ingest.
"""

from __future__ import annotations

import json
import subprocess

from dsm.cli.commands import run_match
from dsm.models import ExclusionReason, NoMatchResult, ShortlistResult
from tests.fixtures import role_01, role_02, role_03


def test_e_r01_partial_exclusion_ranks_remaining_four() -> None:
    """E-R01: Aarav excluded on availability (both dates in detail); the other 4 are ranked."""
    candidates, scorecard = role_01()
    result = run_match(candidates, scorecard)

    assert isinstance(result, ShortlistResult)
    assert result.total_eligible == 4
    assert {a.candidate.email for a in result.ranked_assessments} == {
        "karan@example.com",
        "vivaan@example.com",
        "rahul@example.com",
        "vikram@example.com",
    }

    assert len(result.exclusion_log.exclusions) == 1
    aarav = result.exclusion_log.exclusions[0]
    assert aarav.candidate_email == "aarav@example.com"
    assert aarav.reason is ExclusionReason.AVAILABILITY_MISMATCH
    assert "2026-08-01" in aarav.detail  # Aarav's free date
    assert "2026-07-15" in aarav.detail  # role deadline


def test_e_r02_location_filter_excludes_non_chennai_non_remote() -> None:
    """E-R02: Deepa + Nikhil excluded on location; Karan, Rahul, Priya (remote) are ranked."""
    candidates, scorecard = role_02()
    result = run_match(candidates, scorecard)

    assert isinstance(result, ShortlistResult)
    assert result.total_eligible == 3
    assert {a.candidate.email for a in result.ranked_assessments} == {
        "karan@example.com",
        "rahul@example.com",
        "priya@example.com",
    }

    excluded = {e.candidate_email: e.reason for e in result.exclusion_log.exclusions}
    assert excluded == {
        "deepa@example.com": ExclusionReason.LOCATION_MISMATCH,
        "nikhil@example.com": ExclusionReason.LOCATION_MISMATCH,
    }


def test_e_r03_total_exclusion_produces_no_match() -> None:
    """E-R03: ROLE-03 empty pool → NoMatchResult with 3 ordered near-misses."""
    candidates, scorecard = role_03()
    result = run_match(candidates, scorecard)

    assert isinstance(result, NoMatchResult)
    assert [nm.candidate_email for nm in result.near_misses] == [
        "sanjay@example.com",
        "meera@example.com",
        "arjun@example.com",
    ]


def test_cli_match_smoke_runs_through_real_gates_and_rank() -> None:
    """Refinement: `dsm match` over the stub ingest yields valid output via real gates/rank.

    The three stub candidates (FreeNow, RollingOff on the deadline, NewJoiner on the
    deadline) all pass the real gates under a non-co-located role, so the command emits a
    ShortlistResult with three ranked assessments, no exclusions, and a config snapshot.
    """
    completed = subprocess.run(
        ["uv", "run", "dsm", "match", "--role-id", "ROLE-STUB-01"],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)

    assert payload["role_id"] == "ROLE-STUB-01"
    assert len(payload["ranked_assessments"]) == 3
    assert payload["total_eligible"] == 3
    assert payload["exclusion_log"]["exclusions"] == []
    assert payload["config_snapshot"]["top_k"] == 5
