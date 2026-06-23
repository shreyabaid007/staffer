"""Integration tests: drive the orchestrator with injected fixtures (E-R01, E-R02, E-R03).

These exercise the real gate → exact-filter → score → rank (or → no-match) flow via ``run_match``
with fixture inputs and an injected deterministic score predictor (no LLM, no Milvus — recall and
rerank are skipped by passing ``store=None``/``embed_client=None``). The full ``dsm match`` command
wiring is covered in ``tests/cli/test_orchestrator.py``.
"""

from __future__ import annotations

from dsm.cli.commands import run_match
from dsm.config import load_config
from dsm.match.models import ScoreExtraction
from dsm.models import (
    Candidate,
    ExclusionReason,
    NoMatchResult,
    ShortlistResult,
    TargetProfileScorecard,
)
from tests.fixtures import role_01, role_02, role_03

_CONFIG = load_config()


def _predict(scorecard: TargetProfileScorecard, candidate: Candidate) -> ScoreExtraction:
    """Deterministic stand-in for the LLM score seam (fixed sub-scores; no network)."""
    return ScoreExtraction(skill_match_score=0.75, feedback_score=0.6, narrative="ok")


def test_e_r01_partial_exclusion_ranks_remaining_four() -> None:
    """E-R01: Aarav excluded on availability (both dates in detail); the other 4 are ranked."""
    candidates, scorecard = role_01()
    result = run_match(candidates, scorecard, score_predict=_predict, config=_CONFIG)

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
    result = run_match(candidates, scorecard, score_predict=_predict, config=_CONFIG)

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
    result = run_match(candidates, scorecard, score_predict=_predict, config=_CONFIG)

    assert isinstance(result, NoMatchResult)
    assert [nm.candidate_email for nm in result.near_misses] == [
        "sanjay@example.com",
        "meera@example.com",
        "arjun@example.com",
    ]
