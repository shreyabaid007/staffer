"""Tests for dsm/match/clarify.py — B-001/B-002/B-003/B-004/B-005."""

import json
import os
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import dspy
import pytest
from dspy.utils.dummies import DummyLM

from dsm.match import clarify
from dsm.match.clarify import _fallback_parse
from dsm.models import Location, OpenRole, SkillDepth, SkillRequirement
from dsm.pii.pseudonymised_lm import PseudonymisedLM

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "roles"

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


# ---------------------------------------------------------------------------
# B-004: retry-on-validation-failure
# ---------------------------------------------------------------------------


def _good_prediction() -> MagicMock:
    pred = MagicMock()
    pred.hard_depth_skills_json = _KOTLIN_HARD
    pred.desired_skills_json = _SPRING_DESIRED
    pred.location_json = _LOC_BLR
    pred.clarification_notes = "Kotlin is the hard requirement."
    return pred


def _bad_exc() -> Exception:
    return ValueError("simulated LM parse failure")


def test_retry_succeeds_on_second_attempt() -> None:
    # First call raises AdapterParseError; second returns valid prediction → no fallback
    role = _role(required_skills=[SkillRequirement(name="kotlin", depth=SkillDepth.HARD)])
    mock_predictor = MagicMock(side_effect=[_bad_exc(), _good_prediction()])
    with patch("dsm.match.clarify._predictor", mock_predictor):
        scorecard = clarify.clarify_role(role)

    assert "fallback=true" not in (scorecard.clarification_notes or "").lower()
    hard_names = [s.name for s in scorecard.hard_depth_skills]
    assert "kotlin" in hard_names


def test_fallback_activated_when_both_attempts_fail() -> None:
    # Both calls raise AdapterParseError → fallback must activate
    role = _role(
        required_skills=[SkillRequirement(name="kotlin", depth=SkillDepth.HARD)],
        description="Kotlin (expert)",
    )
    mock_predictor = MagicMock(side_effect=[_bad_exc(), _bad_exc()])
    with patch("dsm.match.clarify._predictor", mock_predictor):
        scorecard = clarify.clarify_role(role)

    assert "fallback=true" in (scorecard.clarification_notes or "").lower()
    hard_names = [s.name for s in scorecard.hard_depth_skills]
    assert "kotlin" in hard_names


# ---------------------------------------------------------------------------
# B-005: golden fixtures (auto-discovered from tests/match/fixtures/roles/)
# ---------------------------------------------------------------------------


def _load_role(data: dict) -> OpenRole:
    inp = data["input"]
    from dsm.models import ProficiencyLevel, SkillDepth, SkillRequirement

    return OpenRole(
        role_id=inp["role_id"],
        title=inp["title"],
        required_skills=[
            SkillRequirement(
                name=s["name"],
                depth=SkillDepth(s["depth"]),
                min_proficiency=ProficiencyLevel(s["min_proficiency"])
                if s.get("min_proficiency")
                else None,
            )
            for s in inp["required_skills"]
        ],
        location=Location(**inp["location"]),
        co_location_required=inp["co_location_required"],
        start_date=datetime.strptime(inp["start_date"], "%Y-%m-%d").date(),
        description=inp.get("description"),
    )


_fixture_files = sorted(_FIXTURES_DIR.glob("*.json"))


@pytest.mark.parametrize("fixture_path", _fixture_files, ids=[f.stem for f in _fixture_files])
def test_golden_fixture_mock_lm(fixture_path: Path) -> None:
    data = json.loads(fixture_path.read_text())
    role = _load_role(data)
    lm_response = data["expected_lm_response"]
    assertions = data["assertions"]

    mock_pred = MagicMock()
    mock_pred.hard_depth_skills_json = lm_response["hard_depth_skills_json"]
    mock_pred.desired_skills_json = lm_response["desired_skills_json"]
    mock_pred.location_json = lm_response["location_json"]
    mock_pred.clarification_notes = lm_response["clarification_notes"]

    with patch("dsm.match.clarify._predictor", return_value=mock_pred):
        scorecard = clarify.clarify_role(role)

    hard_names = {s.name for s in scorecard.hard_depth_skills}
    desired_names = {s.name for s in scorecard.desired_skills}

    for key, expected in assertions.items():
        if key.endswith("_in_hard_depth_skills"):
            skill = key.replace("_in_hard_depth_skills", "").replace("_", " ")
            assert (skill in hard_names) == expected, f"{key}: {skill!r} not found in {hard_names}"
        elif key.endswith("_not_in_desired_skills"):
            skill = key.replace("_not_in_desired_skills", "").replace("_", " ")
            assert (skill not in desired_names) == expected, (
                f"{key}: {skill!r} unexpectedly in {desired_names}"
            )
        elif key == "remote_eligible":
            assert scorecard.location.remote_eligible == expected
        elif key == "co_location_required":
            assert scorecard.co_location_required == expected
        elif key == "location_city":
            assert scorecard.location.city == expected


@pytest.mark.skipif(not os.environ.get("DSM_LIVE_LM"), reason="DSM_LIVE_LM not set")
@pytest.mark.parametrize("fixture_path", _fixture_files, ids=[f.stem for f in _fixture_files])
def test_golden_fixture_live_lm(fixture_path: Path) -> None:
    data = json.loads(fixture_path.read_text())
    role = _load_role(data)
    assertions = data["assertions"]

    scorecard = clarify.clarify_role(role)

    hard_names = {s.name for s in scorecard.hard_depth_skills}
    desired_names = {s.name for s in scorecard.desired_skills}

    for key, expected in assertions.items():
        if key.endswith("_in_hard_depth_skills"):
            skill = key.replace("_in_hard_depth_skills", "").replace("_", " ")
            assert (skill in hard_names) == expected
        elif key.endswith("_not_in_desired_skills"):
            skill = key.replace("_not_in_desired_skills", "").replace("_", " ")
            assert (skill not in desired_names) == expected
        elif key == "remote_eligible":
            assert scorecard.location.remote_eligible == expected
        elif key == "co_location_required":
            assert scorecard.co_location_required == expected
        elif key == "location_city":
            assert scorecard.location.city == expected
