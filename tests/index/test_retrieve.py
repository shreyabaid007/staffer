"""Tests for the exact hard-skill filter (B-1 T-010; FR-4; §6.5).

Structural hard-skill matching only — set membership + proficiency floor, never adjacency.
Deterministic, LLM-free.
"""

from __future__ import annotations

from dsm.index.retrieve import exact_hard_skill_filter
from dsm.models import (
    Candidate,
    CandidateSource,
    EligiblePool,
    ExclusionReason,
    FeedbackSignals,
    FreeNow,
    Location,
    ProficiencyLevel,
    Skill,
    SkillDepth,
    SkillRequirement,
)


def _candidate(email: str, skills: list[tuple[str, ProficiencyLevel]]) -> Candidate:
    return Candidate(
        email=email,
        name="Test",
        location=Location(city="Chennai"),
        availability=FreeNow(),
        skills=[Skill(name=n, proficiency=p) for n, p in skills],
        feedback=FeedbackSignals(),
        source=CandidateSource.BEACH,
    )


def _pool(*candidates: Candidate) -> EligiblePool:
    return EligiblePool(candidates=list(candidates), scorecard_id="ROLE-TEST")


def _hard(name: str, floor: ProficiencyLevel | None = None) -> SkillRequirement:
    return SkillRequirement(name=name, depth=SkillDepth.HARD, min_proficiency=floor)


def test_fr_4_ac_1_subset_passes_and_missing_excluded() -> None:
    """FR-4-AC-1: a candidate holding all hard skills passes; one missing a skill is excluded."""
    has = _candidate("has@x.com", [("kotlin", ProficiencyLevel.ADVANCED)])
    lacks = _candidate("lacks@x.com", [("java", ProficiencyLevel.ADVANCED)])
    pool, exclusions = exact_hard_skill_filter(_pool(has, lacks), [_hard("kotlin")])
    assert [c.email for c in pool.candidates] == ["has@x.com"]
    assert [e.candidate_email for e in exclusions] == ["lacks@x.com"]
    assert pool.scorecard_id == "ROLE-TEST"


def test_fr_4_ac_2_proficiency_floor_excludes_below() -> None:
    """FR-4-AC-2: INTERMEDIATE required, candidate holds BEGINNER → excluded."""
    cand = _candidate("c@x.com", [("python", ProficiencyLevel.BEGINNER)])
    pool, exclusions = exact_hard_skill_filter(
        _pool(cand), [_hard("python", ProficiencyLevel.INTERMEDIATE)]
    )
    assert pool.candidates == []
    assert len(exclusions) == 1
    assert "below proficiency floor" in exclusions[0].detail
    assert "python" in exclusions[0].detail


def test_proficiency_exactly_at_floor_passes() -> None:
    """Edge (design §5): proficiency exactly at the floor passes (≥ is inclusive)."""
    cand = _candidate("c@x.com", [("python", ProficiencyLevel.INTERMEDIATE)])
    pool, exclusions = exact_hard_skill_filter(
        _pool(cand), [_hard("python", ProficiencyLevel.INTERMEDIATE)]
    )
    assert [c.email for c in pool.candidates] == ["c@x.com"]
    assert exclusions == []


def test_proficiency_above_floor_passes() -> None:
    cand = _candidate("c@x.com", [("python", ProficiencyLevel.EXPERT)])
    pool, _ = exact_hard_skill_filter(_pool(cand), [_hard("python", ProficiencyLevel.ADVANCED)])
    assert [c.email for c in pool.candidates] == ["c@x.com"]


def test_fr_4_ac_3_adjacency_never_consulted() -> None:
    """FR-4-AC-3: holding an adjacent skill (java) does not clear a hard kotlin requirement."""
    cand = _candidate(
        "c@x.com", [("java", ProficiencyLevel.EXPERT)]
    )  # adjacent to kotlin (AD-035)
    pool, exclusions = exact_hard_skill_filter(_pool(cand), [_hard("kotlin")])
    assert pool.candidates == []
    assert exclusions[0].reason is ExclusionReason.HARD_SKILL_MISMATCH
    assert "kotlin" in exclusions[0].detail


def test_fr_4_ac_4_exclusion_is_hard_skill_mismatch_with_detail() -> None:
    """FR-4-AC-4: the exclusion reason is HARD_SKILL_MISMATCH and detail names the gap."""
    cand = _candidate("c@x.com", [("aws", ProficiencyLevel.ADVANCED)])
    _, exclusions = exact_hard_skill_filter(_pool(cand), [_hard("kotlin"), _hard("react")])
    assert len(exclusions) == 1
    exclusion = exclusions[0]
    assert exclusion.reason is ExclusionReason.HARD_SKILL_MISMATCH
    assert exclusion.candidate_email == "c@x.com"
    assert "kotlin" in exclusion.detail
    assert "react" in exclusion.detail


def test_fr_4_ac_5_empty_pool_yields_no_exception() -> None:
    """FR-4-AC-5: an empty pool returns an empty pool + no exclusions (no exception)."""
    pool, exclusions = exact_hard_skill_filter(_pool(), [_hard("kotlin")])
    assert pool.candidates == []
    assert exclusions == []
    assert pool.scorecard_id == "ROLE-TEST"


def test_empty_skills_candidate_is_excluded() -> None:
    """Edge (design §5): a candidate with no skills fails any non-empty hard requirement."""
    cand = _candidate("c@x.com", [])
    pool, exclusions = exact_hard_skill_filter(_pool(cand), [_hard("kotlin")])
    assert pool.candidates == []
    assert len(exclusions) == 1


def test_no_hard_skills_passes_everyone() -> None:
    """An empty hard-skill list imposes no requirement → all candidates pass."""
    a = _candidate("a@x.com", [("kotlin", ProficiencyLevel.BEGINNER)])
    b = _candidate("b@x.com", [])
    pool, exclusions = exact_hard_skill_filter(_pool(a, b), [])
    assert [c.email for c in pool.candidates] == ["a@x.com", "b@x.com"]
    assert exclusions == []


def test_multiple_hard_skills_all_required() -> None:
    """All hard-skill names must be present; holding only some → excluded."""
    cand = _candidate(
        "c@x.com",
        [("kotlin", ProficiencyLevel.EXPERT), ("kafka", ProficiencyLevel.ADVANCED)],
    )
    passes, no_excl = exact_hard_skill_filter(_pool(cand), [_hard("kotlin"), _hard("kafka")])
    assert [c.email for c in passes.candidates] == ["c@x.com"]
    assert no_excl == []
    fails, excl = exact_hard_skill_filter(_pool(cand), [_hard("kotlin"), _hard("aws")])
    assert fails.candidates == []
    assert "aws" in excl[0].detail
