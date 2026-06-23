"""Tests for dsm.match.clarify (b-002 T-006; FR-2; §6.2).

Echo (no free text / no predictor) · bounded LLM refine (mocked predictor) · LLM-failure fallback.
No live network — the LM is injected as a ``predict`` callable. No redaction (demand isn't PII).
"""

from __future__ import annotations

from datetime import date

from dsm.match.clarify import clarify_role
from dsm.match.models import ScorecardClarification
from dsm.models import Location, OpenRole, ProficiencyLevel, SkillDepth, SkillRequirement

_KOTLIN_HARD = SkillRequirement(
    name="kotlin", depth=SkillDepth.HARD, min_proficiency=ProficiencyLevel.ADVANCED
)
_AWS_DESIRED = SkillRequirement(name="aws", depth=SkillDepth.DESIRED)


def _role(*, description: str | None = None) -> OpenRole:
    return OpenRole(
        role_id="ROLE-01",
        title="Backend Engineer",
        required_skills=[_KOTLIN_HARD, _AWS_DESIRED],
        location=Location(city="Chennai"),
        co_location_required=True,
        start_date=date(2026, 7, 1),
        description=description,
    )


class TestEchoPath:
    def test_no_predictor_partitions_by_depth(self) -> None:
        sc = clarify_role(_role(description="must have led a payments platform"))  # no predict
        assert sc.hard_depth_skills == [_KOTLIN_HARD]
        assert sc.desired_skills == [_AWS_DESIRED]
        assert sc.clarification_notes is None

    def test_empty_description_echoes_even_with_predictor(self) -> None:
        def _boom(role: OpenRole) -> ScorecardClarification:
            raise AssertionError("predictor must not run when there is no free text")

        sc = clarify_role(_role(description="   "), predict=_boom)
        assert sc.hard_depth_skills == [_KOTLIN_HARD]


class TestLLMPath:
    def test_refined_skills_and_notes_used_gates_from_role(self) -> None:
        refined = ScorecardClarification(
            hard_depth_skills=[
                _KOTLIN_HARD,
                SkillRequirement(name="kafka", depth=SkillDepth.HARD),
            ],
            desired_skills=[_AWS_DESIRED],
            clarification_notes="Payments platform leadership required.",
        )

        sc = clarify_role(_role(description="must have led payments"), predict=lambda r: refined)

        assert {s.name for s in sc.hard_depth_skills} == {"kotlin", "kafka"}
        assert sc.clarification_notes == "Payments platform leadership required."
        # gate fields come from the role, never the LLM (§6.2)
        assert sc.location == Location(city="Chennai")
        assert sc.co_location_required is True
        assert sc.start_date == date(2026, 7, 1)

    def test_llm_failure_falls_back_to_echo(self) -> None:
        def _fail(role: OpenRole) -> ScorecardClarification:
            raise RuntimeError("LM timeout")

        sc = clarify_role(_role(description="must have led payments"), predict=_fail)
        assert sc.hard_depth_skills == [_KOTLIN_HARD]
        assert sc.desired_skills == [_AWS_DESIRED]
        assert sc.clarification_notes is None
