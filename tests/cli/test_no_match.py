"""Orchestrator no-match path tests (O-NM-1..5, E-R03)."""

from __future__ import annotations

import json
from datetime import date

import pytest

import dsm.cli.commands as commands
from dsm.cli.commands import build_closest_on_skills, build_near_misses, run_match
from dsm.config import load_config
from dsm.index.retrieve import exact_hard_skill_filter
from dsm.match.gates import filter_candidates
from dsm.match.models import ScoreExtraction
from dsm.models import (
    AvailabilityState,
    Candidate,
    CandidateSource,
    ExclusionLog,
    ExclusionReason,
    FeedbackSignals,
    FreeNow,
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


def test_c007_excluded_candidate_is_not_a_near_miss() -> None:
    """c-007 FR-3-AC-5: a query-excluded candidate is never surfaced as a near-miss.

    The Chennai candidate CLEARS the hard skill (absent the exclusion they'd be a near-miss), but
    "not Chennai" is non-negotiable — build_near_misses must skip them.
    """
    scorecard = TargetProfileScorecard(
        role_id="ROLE-NEG",
        hard_depth_skills=[SkillRequirement(name="python", depth=SkillDepth.HARD)],
        desired_skills=[],
        location=Location(city=None),
        co_location_required=False,  # distributed "anywhere but Chennai"
        exclude_cities=frozenset({"chennai"}),
        start_date=date(2026, 7, 1),
        availability_window_days=14,
    )
    excluded = Candidate(
        email="cid:x",
        name="X",
        location=Location(city="Chennai"),
        availability=FreeNow(),
        skills=[Skill(name="python", proficiency=ProficiencyLevel.ADVANCED)],
        feedback=FeedbackSignals(),
        source=CandidateSource.BEACH,
    )
    _, log = filter_candidates([excluded], scorecard)
    assert log.exclusions[0].reason is ExclusionReason.LOCATION_MISMATCH
    assert (
        build_near_misses([excluded], scorecard, log) == []
    )  # excluded → not "one decision away"


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
    free_now: bool = False,
) -> Candidate:
    """A RollingOff candidate one day late (or FreeNow if ``free_now``), one optional skill."""
    availability: AvailabilityState = (
        FreeNow() if free_now else RollingOff(expected_date=free, confidence="high")
    )
    return Candidate(
        email=email,
        name=email.split("@")[0].title(),
        location=Location(city=city),
        availability=availability,
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

    # Every ROLE-03 candidate holds java (the sole hard skill), so all four are near-misses with
    # plain negotiable-gap wording (AD-099: skill-clearers only; no skill suffix).
    assert summaries["sanjay@example.com"] == "available 1 day after deadline"
    assert summaries["meera@example.com"] == "available 31 days after deadline"
    assert summaries["arjun@example.com"] == "in Pune, not in onsite set for Mumbai"


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
    assert payload["near_misses"][0]["gap_summary"] == "available 1 day after deadline"


def test_empty_candidate_list_produces_no_match_with_no_near_misses() -> None:
    """Edge case (design.md): empty input → NoMatchResult with empty near_misses."""
    _, scorecard = role_03()
    result = run_match([], scorecard, score_predict=_predict, config=_CONFIG)
    assert isinstance(result, NoMatchResult)
    assert result.near_misses == []
    assert result.exclusion_log.exclusions == []


# --- AD-099: a near-miss must clear the hard skills (skill-failers are excluded) ---------------


def _near(candidates: list[Candidate], scorecard: TargetProfileScorecard) -> dict[str, str]:
    """email → gap_summary for the near-misses (skill-failers are absent under AD-099)."""
    _, log = filter_candidates(candidates, scorecard)
    return {
        nm.candidate_email: nm.gap_summary for nm in build_near_misses(candidates, scorecard, log)
    }


def test_ad099_availability_miss_with_skill_gap_is_excluded() -> None:
    """A date miss that also lacks a hard skill is NOT a near-miss — a shift wouldn't qualify."""
    scorecard = _java_scorecard([SkillRequirement(name="java", depth=SkillDepth.HARD)])
    near = _near([_cand("nokia@example.com", skill="python")], scorecard)
    assert near == {}  # excluded — fixing the date alone doesn't help


def test_ad099_availability_miss_clearing_skills_is_a_clean_near_miss() -> None:
    """A date miss that holds the hard skill is a near-miss, with plain wording (no suffix)."""
    scorecard = _java_scorecard([SkillRequirement(name="java", depth=SkillDepth.HARD)])
    near = _near([_cand("yes@example.com", skill="java")], scorecard)
    assert near == {"yes@example.com": "available 1 day after deadline"}


def test_ad099_location_miss_clearing_skills_is_a_clean_near_miss() -> None:
    """A location miss holding the hard skill is a near-miss; a skill-failing one is not."""
    scorecard = _java_scorecard([SkillRequirement(name="java", depth=SkillDepth.HARD)])
    candidates = [
        _cand("pune@example.com", skill="java", city="Pune"),  # clears → near-miss
        _cand("pyhd@example.com", skill="python", city="Pune"),  # misses java → excluded
    ]
    near = _near(candidates, scorecard)
    assert near == {"pune@example.com": "in Pune, not in onsite set for Mumbai"}


def test_ad099_empty_hard_skills_keeps_every_gate_miss() -> None:
    """With no hard requirement, everyone clears → all negotiable-gate misses are near-misses."""
    scorecard = _java_scorecard([])
    near = _near([_cand("any@example.com", skill="python")], scorecard)
    assert near == {"any@example.com": "available 1 day after deadline"}


def test_ad099_below_proficiency_floor_is_excluded() -> None:
    """Below-floor edge: holds the skill but below min_proficiency → not a near-miss."""
    scorecard = _java_scorecard(
        [
            SkillRequirement(
                name="java", depth=SkillDepth.HARD, min_proficiency=ProficiencyLevel.ADVANCED
            )
        ]
    )
    cand = _cand("junior@example.com", skill="java", proficiency=ProficiencyLevel.BEGINNER)
    assert _near([cand], scorecard) == {}


def test_ad099_pure_hard_skill_miss_is_not_a_near_miss() -> None:
    """A candidate in the right place + available, missing only a hard skill, is excluded too."""
    scorecard = _java_scorecard([SkillRequirement(name="java", depth=SkillDepth.HARD)])
    # FreeNow + right city → clears both gates; lacks java → HARD_SKILL_MISMATCH.
    cand = _cand("freenow@example.com", skill="python", city="Mumbai", free_now=True)
    result = run_match([cand], scorecard, score_predict=_predict, config=_CONFIG)
    assert isinstance(result, NoMatchResult)
    assert result.near_misses == []  # not a near-miss …
    assert [e.candidate_email for e in result.exclusion_log.exclusions] == ["freenow@example.com"]
    assert result.exclusion_log.exclusions[0].reason is ExclusionReason.HARD_SKILL_MISMATCH


def test_ad099_build_near_misses_is_deterministic() -> None:
    """Determinism: same input → identical near-misses (set + order + gap_summary)."""
    candidates, scorecard = role_03()
    _, log = filter_candidates(candidates, scorecard)
    first = build_near_misses(candidates, scorecard, log)
    second = build_near_misses(candidates, scorecard, log)
    assert first == second


# --- AD-098: LLM selection rationale on the shown near-misses ----------------------------------


def test_ad098_rationale_attached_to_shown_near_misses() -> None:
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


def test_ad098_predictor_error_leaves_rationale_none() -> None:
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


def test_ad098_no_predictor_leaves_rationale_none() -> None:
    """Without a predictor (the pure-unit path), selection_rationale stays None."""
    candidates, scorecard = role_03()
    result = run_match(candidates, scorecard, score_predict=_predict, config=_CONFIG)
    assert isinstance(result, NoMatchResult)
    assert all(nm.selection_rationale is None for nm in result.near_misses)


# --- AD-100: closest_on_skills (cleared both gates, only a hard skill short) -------------------


def _skilled(email: str, skills: list[str], *, city: str = "Mumbai") -> Candidate:
    """A FreeNow candidate (clears availability; Mumbai clears the co-location gate by default)."""
    return Candidate(
        email=email,
        name=email.split("@")[0].title(),
        location=Location(city=city),
        availability=FreeNow(),
        skills=[Skill(name=s, proficiency=ProficiencyLevel.ADVANCED) for s in skills],
        feedback=FeedbackSignals(),
        source=CandidateSource.BEACH,
    )


def _full_log(candidates: list[Candidate], scorecard: TargetProfileScorecard) -> ExclusionLog:
    """Gate + exact-filter exclusions combined — mirrors run_match's no-match exclusion log."""
    elig, gate = filter_candidates(candidates, scorecard)
    _, hard = exact_hard_skill_filter(elig, scorecard.hard_depth_skills)
    return ExclusionLog(exclusions=gate.exclusions + hard)


def test_ad100_skill_short_candidate_is_in_closest_not_near() -> None:
    """A FreeNow in-location candidate missing a hard skill → closest_on_skills, not near."""
    sc = _java_scorecard([SkillRequirement(name="java", depth=SkillDepth.HARD)])
    result = run_match(
        [_skilled("a@x.com", ["python"])], sc, score_predict=_predict, config=_CONFIG
    )
    assert isinstance(result, NoMatchResult)
    assert result.near_misses == []
    assert [nm.candidate_email for nm in result.closest_on_skills] == ["a@x.com"]
    assert result.closest_on_skills[0].gap_summary == "missing 1 hard skill: java"


def test_ad100_ordered_by_fewest_gaps_then_email() -> None:
    """closest_on_skills ranks fewest hard-skill gaps first, then email."""
    sc = _java_scorecard(
        [
            SkillRequirement(name="java", depth=SkillDepth.HARD),
            SkillRequirement(name="kafka", depth=SkillDepth.HARD),
        ]
    )
    candidates = [
        _skilled("two@x.com", ["python"]),  # missing java + kafka (2)
        _skilled("one@x.com", ["java"]),  # missing kafka only (1)
    ]
    closest = build_closest_on_skills(candidates, sc, _full_log(candidates, sc))
    assert [nm.candidate_email for nm in closest] == ["one@x.com", "two@x.com"]
    assert closest[0].gap_summary == "missing 1 hard skill: kafka"


def test_ad100_capped_at_three() -> None:
    """closest_on_skills is capped at 3 (AD-063d)."""
    sc = _java_scorecard([SkillRequirement(name="java", depth=SkillDepth.HARD)])
    candidates = [_skilled(f"c{i}@x.com", ["python"]) for i in range(5)]
    result = run_match(candidates, sc, score_predict=_predict, config=_CONFIG)
    assert isinstance(result, NoMatchResult)
    assert len(result.closest_on_skills) == 3


def test_ad100_double_miss_in_neither_list() -> None:
    """A late AND skill-short candidate is in neither list — only the exclusion log."""
    sc = _java_scorecard([SkillRequirement(name="java", depth=SkillDepth.HARD)])
    # RollingOff one day late (availability miss) AND holds python not java.
    result = run_match(
        [_cand("d@x.com", skill="python")], sc, score_predict=_predict, config=_CONFIG
    )
    assert isinstance(result, NoMatchResult)
    assert result.near_misses == []
    assert result.closest_on_skills == []
    assert [e.candidate_email for e in result.exclusion_log.exclusions] == ["d@x.com"]


def test_ad100_near_and_closest_are_disjoint() -> None:
    """Mixed no-match: a skill-clearing late candidate (near) + a skill-short FreeNow (closest)."""
    sc = _java_scorecard([SkillRequirement(name="java", depth=SkillDepth.HARD)])
    candidates = [
        _cand("late@x.com", skill="java"),  # late but holds java → near-miss (AD-099)
        _skilled("short@x.com", ["python"]),  # FreeNow, lacks java → closest_on_skills
    ]
    result = run_match(candidates, sc, score_predict=_predict, config=_CONFIG)
    assert isinstance(result, NoMatchResult)
    assert [nm.candidate_email for nm in result.near_misses] == ["late@x.com"]
    assert [nm.candidate_email for nm in result.closest_on_skills] == ["short@x.com"]
    near = {nm.candidate_email for nm in result.near_misses}
    closest = {nm.candidate_email for nm in result.closest_on_skills}
    assert near.isdisjoint(closest)


def test_ad100_rationale_attached_to_closest() -> None:
    """FR-12-AC-3: the reused predictor annotates shown closest_on_skills entries."""
    sc = _java_scorecard([SkillRequirement(name="java", depth=SkillDepth.HARD)])

    def _rationale(scorecard: TargetProfileScorecard, candidate: Candidate, gap: str) -> str:
        return f"strong {candidate.skills[0].name}, could ramp on java"

    result = run_match(
        [_skilled("a@x.com", ["python"])],
        sc,
        score_predict=_predict,
        config=_CONFIG,
        near_miss_predict=_rationale,
    )
    assert isinstance(result, NoMatchResult)
    assert result.closest_on_skills[0].selection_rationale == "strong python, could ramp on java"


def test_ad100_below_floor_wording() -> None:
    """A held-but-below-floor hard skill surfaces with 'below required proficiency' wording."""
    sc = _java_scorecard(
        [
            SkillRequirement(
                name="java", depth=SkillDepth.HARD, min_proficiency=ProficiencyLevel.EXPERT
            )
        ]
    )
    cand = Candidate(
        email="jr@x.com",
        name="Jr",
        location=Location(city="Mumbai"),
        availability=FreeNow(),
        skills=[Skill(name="java", proficiency=ProficiencyLevel.INTERMEDIATE)],
        feedback=FeedbackSignals(),
        source=CandidateSource.BEACH,
    )
    result = run_match([cand], sc, score_predict=_predict, config=_CONFIG)
    assert isinstance(result, NoMatchResult)
    summary = result.closest_on_skills[0].gap_summary
    assert "below required proficiency" in summary
    assert "java" in summary


def test_ad100_gate_only_no_match_has_empty_closest() -> None:
    """FR-13-AC-2: a gate-only no-match (nobody cleared the gates) → closest_on_skills empty."""
    sc = _java_scorecard([SkillRequirement(name="java", depth=SkillDepth.HARD)])
    # Pune candidate fails the Mumbai co-location gate → excluded before the skill filter.
    result = run_match(
        [_skilled("p@x.com", ["java"], city="Pune")], sc, score_predict=_predict, config=_CONFIG
    )
    assert isinstance(result, NoMatchResult)
    assert result.closest_on_skills == []
    assert [e.reason for e in result.exclusion_log.exclusions] == [
        ExclusionReason.LOCATION_MISMATCH
    ]


def test_ad100_empty_input_has_empty_closest() -> None:
    _, sc = role_03()
    result = run_match([], sc, score_predict=_predict, config=_CONFIG)
    assert isinstance(result, NoMatchResult)
    assert result.closest_on_skills == []
