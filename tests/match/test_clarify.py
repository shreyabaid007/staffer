"""Tests for dsm.match.clarify (B-002 T-005; FR-1)."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import dspy

from dsm.match.clarify import (
    _parse_desired_skills,
    _parse_hard_skills,
    clarify_role,
)
from dsm.models import (
    Location,
    OpenRole,
    ProficiencyLevel,
    SkillDepth,
    SkillRequirement,
)

_ROLE_LOCATION = Location(city="Pune")
_START = date(2026, 7, 1)


def _role(
    *,
    description: str | None = None,
    skills: list[SkillRequirement] | None = None,
) -> OpenRole:
    return OpenRole(
        role_id="R-001",
        title="Senior Engineer",
        required_skills=skills
        or [
            SkillRequirement(name="kotlin", depth=SkillDepth.HARD),
            SkillRequirement(name="react", depth=SkillDepth.DESIRED),
        ],
        location=_ROLE_LOCATION,
        co_location_required=True,
        start_date=_START,
        description=description,
    )


class TestEchoPath:
    def test_empty_description_echoes(self) -> None:
        sc = clarify_role(_role(description=None))
        assert sc.role_id == "R-001"
        assert len(sc.hard_depth_skills) == 1
        assert sc.hard_depth_skills[0].name == "kotlin"
        assert len(sc.desired_skills) == 1
        assert sc.desired_skills[0].name == "react"
        assert sc.clarification_notes is None

    def test_whitespace_description_echoes(self) -> None:
        sc = clarify_role(_role(description="   "))
        assert sc.clarification_notes is None
        assert len(sc.hard_depth_skills) == 1

    def test_description_but_no_lm_echoes(self) -> None:
        sc = clarify_role(_role(description="Needs deep Kotlin experience"), lm=None)
        assert sc.clarification_notes is None

    def test_echo_preserves_location_and_timing(self) -> None:
        sc = clarify_role(_role())
        assert sc.location == _ROLE_LOCATION
        assert sc.co_location_required is True
        assert sc.start_date == _START
        assert sc.availability_window_days == 14


class TestLLMPath:
    def test_llm_path_parses_response(self) -> None:
        mock_lm = MagicMock(spec=dspy.LM)
        mock_result = MagicMock()
        mock_result.hard_skills = "kotlin:expert; java:advanced"
        mock_result.desired_skills = "react; docker"
        mock_result.clarification_notes = "Role needs deep JVM expertise."

        with patch("dsm.match.clarify.dspy.Predict") as mock_predict_cls:
            mock_predictor = MagicMock()
            mock_predictor.return_value = mock_result
            mock_predict_cls.return_value = mock_predictor

            sc = clarify_role(
                _role(description="Needs deep Kotlin and Java experience"),
                lm=mock_lm,
            )

        assert len(sc.hard_depth_skills) == 2
        assert sc.hard_depth_skills[0].name == "kotlin"
        assert sc.hard_depth_skills[0].min_proficiency == ProficiencyLevel.EXPERT
        assert sc.hard_depth_skills[1].name == "java"
        assert sc.hard_depth_skills[1].min_proficiency == ProficiencyLevel.ADVANCED
        assert len(sc.desired_skills) == 2
        assert sc.desired_skills[0].name == "react"
        assert sc.desired_skills[1].name == "docker"
        assert sc.clarification_notes == "Role needs deep JVM expertise."

    def test_llm_failure_falls_back_to_echo(self) -> None:
        mock_lm = MagicMock(spec=dspy.LM)

        with patch("dsm.match.clarify.dspy.Predict") as mock_predict_cls:
            mock_predictor = MagicMock()
            mock_predictor.side_effect = RuntimeError("LLM timeout")
            mock_predict_cls.return_value = mock_predictor

            sc = clarify_role(
                _role(description="Needs deep Kotlin experience"),
                lm=mock_lm,
            )

        assert sc.clarification_notes is None
        assert len(sc.hard_depth_skills) == 1
        assert sc.hard_depth_skills[0].name == "kotlin"

    def test_description_passed_verbatim_no_redaction(self) -> None:
        """§7: demand-side description carries no candidate PII; passed verbatim."""
        raw_desc = "Must have 5+ years Kotlin, strong AWS, ideally with Terraform"
        mock_lm = MagicMock(spec=dspy.LM)
        mock_result = MagicMock()
        mock_result.hard_skills = "kotlin:advanced"
        mock_result.desired_skills = "terraform"
        mock_result.clarification_notes = "Notes."

        with patch("dsm.match.clarify.dspy.Predict") as mock_predict_cls:
            mock_predictor = MagicMock()
            mock_predictor.return_value = mock_result
            mock_predict_cls.return_value = mock_predictor

            clarify_role(_role(description=raw_desc), lm=mock_lm)

            call_kwargs = mock_predictor.call_args[1]
            assert call_kwargs["description"] == raw_desc


class TestParsers:
    def test_parse_hard_skills(self) -> None:
        result = _parse_hard_skills("kotlin:expert; java:advanced; python")
        assert len(result) == 3
        assert result[0].name == "kotlin"
        assert result[0].min_proficiency == ProficiencyLevel.EXPERT
        assert result[0].depth == SkillDepth.HARD
        assert result[2].name == "python"
        assert result[2].min_proficiency is None

    def test_parse_hard_skills_empty(self) -> None:
        assert _parse_hard_skills("") == []
        assert _parse_hard_skills("  ") == []

    def test_parse_desired_skills(self) -> None:
        result = _parse_desired_skills("react; docker; kubernetes")
        assert len(result) == 3
        assert all(s.depth == SkillDepth.DESIRED for s in result)
        assert result[0].name == "react"

    def test_parse_desired_skills_empty(self) -> None:
        assert _parse_desired_skills("") == []

    def test_parse_hard_skills_invalid_proficiency_becomes_none(self) -> None:
        result = _parse_hard_skills("kotlin:mastery")
        assert result[0].min_proficiency is None
