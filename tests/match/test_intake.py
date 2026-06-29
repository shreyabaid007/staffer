"""Tests for dsm.match.intake (c-006 T-002; FR-1/FR-2/FR-4/FR-6/FR-8; § A1/A3).

Pure assembly + deterministic validation — no live network. The LLM seam is the parsed
``RoleIntake`` itself (what ``make_intake_predictor`` would return), built directly here.
"""

from __future__ import annotations

from datetime import date, timedelta

import dspy

from dsm.match.intake import (
    ClarificationNeeded,
    NullIntakeCache,
    _intake_module,
    assemble_role,
    intake_cache_key,
    resolve_start_date,
)
from dsm.match.models import RoleIntake
from dsm.models import OpenRole, ProficiencyLevel, SkillDepth, SkillRequirement

_TODAY = date(2026, 6, 29)
_HORIZON = 730


def _assemble(intake: RoleIntake, *, today: date = _TODAY) -> OpenRole | ClarificationNeeded:
    return assemble_role(intake, today, max_horizon_days=_HORIZON, role_id="NL-test1234")


# ---------------------------------------------------------------------------
# Real-style phrasings → correct OpenRole (the "say it out loud" fixtures, §8)
# ---------------------------------------------------------------------------


class TestAssembleHappyPaths:
    def test_kotlin_chennai_payments_next_month(self) -> None:
        # "senior Kotlin engineer in Chennai, payments, starting next month" — relative date (FR-2)
        intake = RoleIntake(
            title="Senior Kotlin Engineer",
            hard_skills=[
                SkillRequirement(
                    name="kotlin", depth=SkillDepth.HARD, min_proficiency=ProficiencyLevel.ADVANCED
                )
            ],
            location_city="Chennai",
            start_date_iso="2026-07-29",
            start_date_phrase="next month",
            notes="Payments domain.",
        )
        role = _assemble(intake)
        assert isinstance(role, OpenRole)
        assert role.title == "Senior Kotlin Engineer"
        assert role.location.city == "Chennai"  # case preserved (gate is case-insensitive)
        assert role.co_location_required is True  # derived: named city, not remote (FR-8)
        assert role.start_date == date(2026, 7, 29)
        assert [(s.name, s.depth) for s in role.required_skills] == [("kotlin", SkillDepth.HARD)]
        assert role.description == "Payments domain."

    def test_react_remote_available_now(self) -> None:
        # "React dev, remote India, available now" — remote ⇒ co_location False (FR-8)
        intake = RoleIntake(
            title="React Developer",
            hard_skills=[SkillRequirement(name="react", depth=SkillDepth.HARD)],
            remote_within_country=True,
            start_date_iso=_TODAY.isoformat(),
            start_date_phrase="available now",
        )
        role = _assemble(intake)
        assert isinstance(role, OpenRole)
        assert role.location.city is None
        assert role.location.remote_within_country is True
        assert role.co_location_required is False
        assert role.start_date == _TODAY

    def test_desired_skills_land_as_desired(self) -> None:
        intake = RoleIntake(
            title="Data Engineer",
            hard_skills=[SkillRequirement(name="spark", depth=SkillDepth.HARD)],
            desired_skills=[SkillRequirement(name="airflow", depth=SkillDepth.DESIRED)],
            location_city="Bengaluru",
            start_date_iso="2026-07-20",
            start_date_phrase="in 3 weeks",
        )
        role = _assemble(intake)
        assert isinstance(role, OpenRole)
        depths = {s.name: s.depth for s in role.required_skills}
        assert depths == {"spark": SkillDepth.HARD, "airflow": SkillDepth.DESIRED}


# ---------------------------------------------------------------------------
# FR-8: co_location_required is Python-derived, never an LLM field
# ---------------------------------------------------------------------------


class TestCoLocationDerived:
    def test_named_city_implies_onsite(self) -> None:
        role = _assemble(
            RoleIntake(
                location_city="Pune", start_date_iso="2026-08-01", start_date_phrase="Aug 1"
            )
        )
        assert isinstance(role, OpenRole)
        assert role.co_location_required is True

    def test_remote_is_not_onsite(self) -> None:
        role = _assemble(RoleIntake(remote_within_country=True, start_date_iso="2026-08-01"))
        assert isinstance(role, OpenRole)
        assert role.co_location_required is False

    def test_role_intake_cannot_carry_a_co_location_value(self) -> None:
        # The field does not exist on RoleIntake, so no LLM value can ever reach the gate (FR-8).
        assert "co_location_required" not in RoleIntake.model_fields


# ---------------------------------------------------------------------------
# FR-2: relative-date resolution validated deterministically
# ---------------------------------------------------------------------------


class TestStartDateValidation:
    def test_valid_iso_in_window_is_used(self) -> None:
        assert resolve_start_date("2026-07-29", _TODAY, max_horizon_days=_HORIZON) == date(
            2026, 7, 29
        )

    def test_absent_is_none(self) -> None:
        assert resolve_start_date(None, _TODAY, max_horizon_days=_HORIZON) is None
        assert resolve_start_date("", _TODAY, max_horizon_days=_HORIZON) is None

    def test_malformed_iso_is_rejected(self) -> None:
        assert resolve_start_date("2026-13-40", _TODAY, max_horizon_days=_HORIZON) is None
        assert resolve_start_date("next month", _TODAY, max_horizon_days=_HORIZON) is None

    def test_before_today_is_rejected(self) -> None:
        assert resolve_start_date("2026-06-28", _TODAY, max_horizon_days=_HORIZON) is None

    def test_beyond_horizon_is_rejected(self) -> None:
        far = (_TODAY + timedelta(days=_HORIZON + 1)).isoformat()
        assert resolve_start_date(far, _TODAY, max_horizon_days=_HORIZON) is None

    def test_malformed_date_triggers_start_clarification(self) -> None:
        result = _assemble(RoleIntake(location_city="Chennai", start_date_iso="not-a-date"))
        assert isinstance(result, ClarificationNeeded)
        assert result.missing == ["start"]

    def test_out_of_window_date_triggers_start_clarification(self) -> None:
        far = (_TODAY + timedelta(days=_HORIZON + 5)).isoformat()
        result = _assemble(RoleIntake(location_city="Chennai", start_date_iso=far))
        assert isinstance(result, ClarificationNeeded)
        assert result.missing == ["start"]  # FR-2-AC-3 at the assemble level, not just resolve


# ---------------------------------------------------------------------------
# FR-4: missing required gate field → ClarificationNeeded (never a guess)
# ---------------------------------------------------------------------------


class TestClarificationNeeded:
    def test_missing_location_asks_to_clarify(self) -> None:
        # "backend engineer, Java and Spring Boot, starting 2026-08-01" with NO location
        intake = RoleIntake(
            title="Backend Engineer",
            hard_skills=[
                SkillRequirement(name="java", depth=SkillDepth.HARD),
                SkillRequirement(name="spring boot", depth=SkillDepth.HARD),
            ],
            start_date_iso="2026-08-01",
            start_date_phrase="2026-08-01",
        )
        result = _assemble(intake)
        assert isinstance(result, ClarificationNeeded)
        assert result.missing == ["location"]
        assert result.partial is intake  # carries what was parsed, for re-assembly

    def test_both_missing_lists_both(self) -> None:
        result = _assemble(RoleIntake(title="Engineer"))
        assert isinstance(result, ClarificationNeeded)
        assert result.missing == ["location", "start"]

    def test_no_location_is_not_invented(self) -> None:
        """Never-guess (FR-1-AC-4): absent location stays absent, no hallucinated city."""
        result = _assemble(RoleIntake(start_date_iso="2026-08-01"))
        assert isinstance(result, ClarificationNeeded)
        assert "location" in result.missing


# ---------------------------------------------------------------------------
# FR-1-AC-5: forced skill depth from the bucket
# ---------------------------------------------------------------------------


def test_forced_depth_overrides_mis_bucketed_element() -> None:
    """A skill placed in hard_skills but tagged DESIRED still lands HARD (bucket wins)."""
    intake = RoleIntake(
        hard_skills=[
            SkillRequirement(name="Kotlin", depth=SkillDepth.DESIRED)
        ],  # wrong depth + caps
        location_city="Chennai",
        start_date_iso="2026-08-01",
    )
    role = _assemble(intake)
    assert isinstance(role, OpenRole)
    (skill,) = role.required_skills
    assert skill.name == "kotlin"  # normalised lowercase
    assert skill.depth == SkillDepth.HARD  # forced from the bucket


# ---------------------------------------------------------------------------
# FR-6: cache-key stability + derivation-version sensitivity
# ---------------------------------------------------------------------------


class TestCacheKey:
    def test_same_inputs_same_key(self) -> None:
        a = intake_cache_key("Kotlin in Chennai", _TODAY, "m1", "intake-v1")
        b = intake_cache_key(
            "  kotlin   in chennai ", _TODAY, "m1", "intake-v1"
        )  # cosmetic variant
        assert a == b

    def test_prompt_version_change_changes_key(self) -> None:
        a = intake_cache_key("q", _TODAY, "m1", "intake-v1")
        b = intake_cache_key("q", _TODAY, "m1", "intake-v2")
        assert a != b

    def test_model_change_changes_key(self) -> None:
        a = intake_cache_key("q", _TODAY, "m1", "intake-v1")
        b = intake_cache_key("q", _TODAY, "m2", "intake-v1")
        assert a != b

    def test_run_date_change_changes_key(self) -> None:
        a = intake_cache_key("q", _TODAY, "m1", "intake-v1")
        b = intake_cache_key("q", _TODAY + timedelta(days=1), "m1", "intake-v1")
        assert a != b

    def test_null_cache_always_misses(self) -> None:
        cache = NullIntakeCache()
        cache.put("k", RoleIntake(title="x"))
        assert cache.get("k") is None


# ---------------------------------------------------------------------------
# NF-6: the predictor is a bare dspy.Predict (compileable), no baked demos
# ---------------------------------------------------------------------------


def test_intake_module_is_bare_predict_no_demos() -> None:
    module = _intake_module()
    assert isinstance(module, dspy.Predict)
    assert not module.demos  # no few-shot baked in → can be compiled offline (MIPROv2)
