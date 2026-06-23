"""Orchestrator no-match path tests (O-NM-1..5, E-R03)."""

from __future__ import annotations

import json
from datetime import date

import pytest

import dsm.cli.commands as commands
from dsm.cli.commands import build_near_misses, run_match
from dsm.config import load_config
from dsm.match.gates import filter_candidates
from dsm.match.models import ScoreExtraction
from dsm.models import (
    Candidate,
    CandidateSource,
    ExclusionReason,
    FeedbackSignals,
    Location,
    NoMatchResult,
    ProficiencyLevel,
    RollingOff,
    Skill,
    SkillDepth,
    SkillRequirement,
    TargetProfileScorecard,
)
from tests.fixtures import role_03

_CONFIG = load_config()
_ONE_DAY_LATE = date(2026, 7, 16)  # one day past ROLE-NM's 2026-07-15 deadline → availability miss


def _predict(scorecard: TargetProfileScorecard, candidate: Candidate) -> ScoreExtraction:
    """Deterministic score seam — never invoked on the no-match path (empty pool)."""
    return ScoreExtraction()


def _java_scorecard(hard: list[SkillRequirement]) -> TargetProfileScorecard:
    """A Mumbai co-location role with the given hard skills; deadline 2026-07-15."""
    return TargetProfileScorecard(
        role_id="ROLE-NM",
        hard_depth_skills=hard,
        desired_skills=[],
        location=Location(city="Mumbai"),
        co_location_required=True,
        start_date=date(2026, 7, 1),
        availability_window_days=14,
    )


def _cand(
    email: str,
    *,
    skill: str | None = "java",
    proficiency: ProficiencyLevel = ProficiencyLevel.ADVANCED,
    city: str = "Mumbai",
    free: date = _ONE_DAY_LATE,
) -> Candidate:
    """A RollingOff candidate one day late, with an optional single skill (default java)."""
    return Candidate(
        email=email,
        name=email.split("@")[0].title(),
        location=Location(city=city),
        availability=RollingOff(expected_date=free, confidence="high"),
        skills=[Skill(name=skill, proficiency=proficiency)] if skill else [],
        feedback=FeedbackSignals(),
        source=CandidateSource.ROLLING_OFF,
    )


def test_e_r03_role_03_returns_no_match_with_ordered_near_misses() -> None:
    """E-R03 / O-NM-1/2/3: ROLE-03 → NoMatchResult, near-misses [Sanjay, Meera, Arjun]."""
    candidates, scorecard = role_03()
    result = run_match(candidates, scorecard, score_predict=_predict, config=_CONFIG)

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
    result = run_match(candidates, scorecard, score_predict=_predict, config=_CONFIG)
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

    # AD-095: every ROLE-03 candidate holds java (the sole hard skill), so each pre-filter miss
    # reports it clears hard skills — the availability misses are genuinely actionable.
    assert (
        summaries["sanjay@example.com"] == "available 1 day after deadline; clears all hard skills"
    )
    assert (
        summaries["meera@example.com"]
        == "available 31 days after deadline; clears all hard skills"
    )
    assert (
        summaries["arjun@example.com"]
        == "in Pune, not in onsite set for Mumbai; clears all hard skills"
    )


def test_o_nm_1_rank_not_called_when_pool_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O-NM-1: the orchestrator builds NoMatchResult and never invokes rank for an empty pool."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("rank_assessments must not be called when the pool is empty")

    monkeypatch.setattr(commands, "rank_assessments", _boom)
    candidates, scorecard = role_03()
    result = run_match(candidates, scorecard, score_predict=_predict, config=_CONFIG)
    assert isinstance(result, NoMatchResult)


def test_o_nm_5_no_match_result_renders_to_json() -> None:
    """O-NM-5: the NoMatchResult (reason + near-misses + gap summaries) serialises for the CLI."""
    candidates, scorecard = role_03()
    result = run_match(candidates, scorecard, score_predict=_predict, config=_CONFIG)
    payload = json.loads(result.model_dump_json())

    assert payload["reason"]
    assert len(payload["near_misses"]) == 3
    assert payload["near_misses"][0]["candidate_email"] == "sanjay@example.com"
    assert (
        payload["near_misses"][0]["gap_summary"]
        == "available 1 day after deadline; clears all hard skills"
    )


def test_empty_candidate_list_produces_no_match_with_no_near_misses() -> None:
    """Edge case (design.md): empty input → NoMatchResult with empty near_misses."""
    _, scorecard = role_03()
    result = run_match([], scorecard, score_predict=_predict, config=_CONFIG)
    assert isinstance(result, NoMatchResult)
    assert result.near_misses == []
    assert result.exclusion_log.exclusions == []


# --- AD-095: hard-skill verdict on pre-skill-filter near-misses --------------------------------


def _gaps(candidates: list[Candidate], scorecard: TargetProfileScorecard) -> dict[str, str]:
    _, log = filter_candidates(candidates, scorecard)
    return {
        nm.candidate_email: nm.gap_summary for nm in build_near_misses(candidates, scorecard, log)
    }


def test_ad095_availability_miss_with_skill_gap_reports_missing_skill() -> None:
    """FR-1-AC-3: an availability miss also lacking a hard skill says so (shift won't help)."""
    scorecard = _java_scorecard([SkillRequirement(name="java", depth=SkillDepth.HARD)])
    gaps = _gaps([_cand("nokia@example.com", skill="python")], scorecard)
    assert (
        gaps["nokia@example.com"]
        == "available 1 day after deadline; also missing 1 hard skill: java"
    )
    assert "clears all hard skills" not in gaps["nokia@example.com"]


def test_ad095_availability_miss_clearing_skills_is_actionable() -> None:
    """FR-1-AC-2: an availability miss holding the hard skill is flagged as clearing skills."""
    scorecard = _java_scorecard([SkillRequirement(name="java", depth=SkillDepth.HARD)])
    gaps = _gaps([_cand("yes@example.com", skill="java")], scorecard)
    assert gaps["yes@example.com"] == "available 1 day after deadline; clears all hard skills"


def test_ad095_location_miss_carries_skill_verdict() -> None:
    """FR-2-AC-1: a location miss appends the same verdict suffix."""
    scorecard = _java_scorecard([SkillRequirement(name="java", depth=SkillDepth.HARD)])
    # FreeNow + wrong city → location miss; holds java → clears.
    cand = _cand("pune@example.com", skill="java", city="Pune")
    gaps = _gaps([cand], scorecard)
    assert (
        gaps["pune@example.com"] == "in Pune, not in onsite set for Mumbai; clears all hard skills"
    )


def test_ad095_empty_hard_skills_reports_cleared() -> None:
    """FR-5: with no hard requirement, every pre-filter miss reports it clears hard skills."""
    scorecard = _java_scorecard([])
    gaps = _gaps([_cand("any@example.com", skill="python")], scorecard)
    assert gaps["any@example.com"] == "available 1 day after deadline; clears all hard skills"


def test_ad095_below_proficiency_floor_counts_as_gap() -> None:
    """Below-floor edge: holds the skill but below min_proficiency → not 'clears'."""
    scorecard = _java_scorecard(
        [
            SkillRequirement(
                name="java", depth=SkillDepth.HARD, min_proficiency=ProficiencyLevel.ADVANCED
            )
        ]
    )
    cand = _cand("junior@example.com", skill="java", proficiency=ProficiencyLevel.BEGINNER)
    gaps = _gaps([cand], scorecard)
    assert "clears all hard skills" not in gaps["junior@example.com"]
    assert "below required proficiency" in gaps["junior@example.com"]


def test_ad095_skill_clearing_availability_miss_ranks_first() -> None:
    """FR-6-AC-1: at equal overshoot, the skill-clearing avail miss ranks above one with a gap."""
    scorecard = _java_scorecard([SkillRequirement(name="java", depth=SkillDepth.HARD)])
    candidates = [
        _cand("gap@example.com", skill="python"),  # same overshoot, but misses java
        _cand("clear@example.com", skill="java"),  # clears
    ]
    _, log = filter_candidates(candidates, scorecard)
    order = [nm.candidate_email for nm in build_near_misses(candidates, scorecard, log)]
    assert order == ["clear@example.com", "gap@example.com"]


def test_ad095_build_near_misses_is_deterministic() -> None:
    """Determinism: same input → identical near-misses (set + order + gap_summary)."""
    candidates, scorecard = role_03()
    _, log = filter_candidates(candidates, scorecard)
    first = build_near_misses(candidates, scorecard, log)
    second = build_near_misses(candidates, scorecard, log)
    assert first == second


# --- AD-096: LLM selection rationale on the shown near-misses ----------------------------------


def test_ad096_rationale_attached_to_shown_near_misses() -> None:
    """FR-9-AC-2: with a predictor injected, each shown near-miss gets a selection_rationale."""
    candidates, scorecard = role_03()
    calls: list[str] = []

    def _rationale(sc: TargetProfileScorecard, candidate: Candidate, gap: str) -> str:
        calls.append(candidate.email)
        return f"strong {candidate.skills[0].name} background"

    result = run_match(
        candidates, scorecard, score_predict=_predict, config=_CONFIG, near_miss_predict=_rationale
    )
    assert isinstance(result, NoMatchResult)
    assert all(nm.selection_rationale for nm in result.near_misses)
    # Exactly the shown top-3 are explained — never the capped-out fourth (FR-9-AC-2).
    assert len(calls) == 3
    assert "kavita@example.com" not in calls


def test_ad096_predictor_error_leaves_rationale_none() -> None:
    """FR-9-AC-4: a predictor failure leaves selection_rationale None; near-miss still shows."""
    candidates, scorecard = role_03()

    def _boom(sc: TargetProfileScorecard, candidate: Candidate, gap: str) -> str:
        raise RuntimeError("LLM down")

    result = run_match(
        candidates, scorecard, score_predict=_predict, config=_CONFIG, near_miss_predict=_boom
    )
    assert isinstance(result, NoMatchResult)
    assert len(result.near_misses) == 3
    assert all(nm.selection_rationale is None for nm in result.near_misses)


def test_ad096_no_predictor_leaves_rationale_none() -> None:
    """Without a predictor (the pure-unit path), selection_rationale stays None."""
    candidates, scorecard = role_03()
    result = run_match(candidates, scorecard, score_predict=_predict, config=_CONFIG)
    assert isinstance(result, NoMatchResult)
    assert all(nm.selection_rationale is None for nm in result.near_misses)
