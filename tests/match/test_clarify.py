"""Tests for dsm/match/clarify.py — B-001/B-002/B-003."""

from datetime import date

import dspy
from dspy.utils.dummies import DummyLM

from dsm.match import clarify
from dsm.match.clarify import _fallback_parse
from dsm.models import Location, OpenRole, SkillDepth, SkillRequirement
from dsm.pii.pseudonymised_lm import PseudonymisedLM

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _role(
    *,
    required_skills: list[SkillRequirement] | None = None,
    description: str | None = None,
    city: str = "Bengaluru",
    remote_eligible: bool = False,
) -> OpenRole:
    return OpenRole(
        role_id="ROLE-TEST",
        title="Test Role",
        required_skills=required_skills or [],
        location=Location(city=city, country="India", remote_eligible=remote_eligible),
        co_location_required=True,
        start_date=date(2026, 8, 1),
        description=description,
    )


def test_clarify_module_imports() -> None:
    assert hasattr(clarify, "ClarifyRole")
    assert hasattr(clarify, "clarify_role")


def test_clarify_role_signature_fields() -> None:
    sig = clarify.ClarifyRole
    assert "role_id" in sig.input_fields
    assert "role_title" in sig.input_fields
    assert "required_skills_raw" in sig.input_fields
    assert "description" in sig.input_fields
    assert "hard_depth_skills_json" in sig.output_fields
    assert "desired_skills_json" in sig.output_fields
    assert "location_json" in sig.output_fields
    assert "clarification_notes" in sig.output_fields


def test_configured_lm_is_pseudonymised() -> None:
    lm = dspy.settings.lm
    assert isinstance(lm, PseudonymisedLM), f"Expected PseudonymisedLM, got {type(lm).__name__}"


# ---------------------------------------------------------------------------
# B-002: deterministic fallback parser
# ---------------------------------------------------------------------------


def test_fallback_expert_marker_in_description() -> None:
    role = _role(description="Kotlin (expert); Spring Boot (nice to have)")
    scorecard = _fallback_parse(role)
    hard_names = [s.name for s in scorecard.hard_depth_skills]
    desired_names = [s.name for s in scorecard.desired_skills]
    assert "kotlin" in hard_names
    assert "spring boot" in desired_names
    assert "kotlin" not in desired_names


def test_fallback_depth_marker_in_skill_name() -> None:
    role = _role(
        required_skills=[
            SkillRequirement(name="kotlin (depth)", depth=SkillDepth.HARD),
            SkillRequirement(name="react (desired)", depth=SkillDepth.DESIRED),
        ]
    )
    scorecard = _fallback_parse(role)
    hard_names = [s.name for s in scorecard.hard_depth_skills]
    desired_names = [s.name for s in scorecard.desired_skills]
    assert "kotlin" in hard_names
    assert "react" in desired_names


def test_fallback_composite_location_sets_remote_eligible() -> None:
    role = _role(description="This role accepts remote-India candidates.")
    scorecard = _fallback_parse(role)
    assert scorecard.location.remote_eligible is True


def test_fallback_empty_description_no_crash() -> None:
    role = _role(description=None)
    scorecard = _fallback_parse(role)
    assert scorecard.role_id == "ROLE-TEST"


def test_fallback_empty_skills_no_crash() -> None:
    role = _role(required_skills=[], description=None)
    scorecard = _fallback_parse(role)
    assert scorecard.hard_depth_skills == []
    assert scorecard.desired_skills == []


def test_fallback_sets_fallback_flag_in_notes() -> None:
    role = _role(description="Kotlin (expert)")
    scorecard = _fallback_parse(role)
    assert scorecard.clarification_notes is not None
    assert "fallback=true" in (scorecard.clarification_notes or "").lower()


def test_fallback_unmarked_skill_defaults_to_hard() -> None:
    role = _role(description="Python; Go")
    scorecard = _fallback_parse(role)
    hard_names = [s.name for s in scorecard.hard_depth_skills]
    assert "python" in hard_names
    assert "go" in hard_names


def test_fallback_hard_skill_not_in_desired(  # AD-033
) -> None:
    role = _role(description="Kotlin (expert); Kotlin (nice to have)")
    scorecard = _fallback_parse(role)
    hard_names = {s.name for s in scorecard.hard_depth_skills}
    desired_names = {s.name for s in scorecard.desired_skills}
    assert "kotlin" in hard_names
    assert "kotlin" not in desired_names


# ---------------------------------------------------------------------------
# B-003: clarify_role predict + Pydantic parse
# ---------------------------------------------------------------------------

_LOC_BLR = '{"city": "Bengaluru", "state": null, "country": "India", "remote_eligible": false}'
_LOC_BLR_REMOTE = (
    '{"city": "Bengaluru", "state": null, "country": "India", "remote_eligible": true}'
)
_KOTLIN_HARD = '[{"name": "kotlin", "depth": "hard", "min_proficiency": null}]'
_SPRING_DESIRED = '[{"name": "spring boot", "depth": "desired", "min_proficiency": null}]'
_KOTLIN_DESIRED = '[{"name": "kotlin", "depth": "desired", "min_proficiency": null}]'
_PYTHON_HARD = '[{"name": "python", "depth": "hard", "min_proficiency": null}]'

_GOOD_PREDICTION = {
    "hard_depth_skills_json": _KOTLIN_HARD,
    "desired_skills_json": _SPRING_DESIRED,
    "location_json": _LOC_BLR,
    "clarification_notes": "Kotlin is the hard requirement; Spring Boot is desirable.",
}

_REMOTE_INDIA_PREDICTION = {
    "hard_depth_skills_json": _PYTHON_HARD,
    "desired_skills_json": "[]",
    "location_json": _LOC_BLR_REMOTE,
    "clarification_notes": "Remote India accepted.",
}


def test_clarify_role_happy_path() -> None:
    role = _role(
        required_skills=[SkillRequirement(name="kotlin", depth=SkillDepth.HARD)],
        description="Kotlin depth is the hard requirement.",
    )
    with dspy.context(lm=DummyLM([_GOOD_PREDICTION])):
        scorecard = clarify.clarify_role(role)

    hard_names = [s.name for s in scorecard.hard_depth_skills]
    desired_names = [s.name for s in scorecard.desired_skills]
    assert "kotlin" in hard_names
    assert "spring boot" in desired_names
    assert scorecard.role_id == "ROLE-TEST"
    assert scorecard.availability_window_days == 14


def test_clarify_role_remote_india_location() -> None:
    role = _role(description="Bengaluru / remote-India candidates welcome.")
    with dspy.context(lm=DummyLM([_REMOTE_INDIA_PREDICTION])):
        scorecard = clarify.clarify_role(role)

    assert scorecard.location.remote_eligible is True


def test_clarify_role_hard_skill_not_in_desired() -> None:
    # LLM mistakenly puts kotlin in both lists; post-parse must strip it from desired (AC-B13)
    prediction = {
        "hard_depth_skills_json": _KOTLIN_HARD,
        "desired_skills_json": _KOTLIN_DESIRED,
        "location_json": _LOC_BLR,
        "clarification_notes": "Kotlin hard.",
    }
    role = _role()
    with dspy.context(lm=DummyLM([prediction])):
        scorecard = clarify.clarify_role(role)

    hard_names = {s.name for s in scorecard.hard_depth_skills}
    desired_names = {s.name for s in scorecard.desired_skills}
    assert "kotlin" in hard_names
    assert "kotlin" not in desired_names


def test_clarify_role_role01_kotlin_in_hard(  # AC-B06 seed eval invariant
) -> None:
    role = _role(
        required_skills=[SkillRequirement(name="kotlin", depth=SkillDepth.HARD)],
        description="Kotlin depth is the hard requirement; payments domain experience needed.",
    )
    with dspy.context(lm=DummyLM([_GOOD_PREDICTION])):
        scorecard = clarify.clarify_role(role)

    hard_names = [s.name for s in scorecard.hard_depth_skills]
    assert "kotlin" in hard_names
    assert all(s.name != "kotlin" for s in scorecard.desired_skills)
