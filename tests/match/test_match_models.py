"""Tests for the query-time demand intermediates (B-1 T-007; ee-query-architecture §6.1)."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from dsm.config import load_prompt
from dsm.match.models import DemandParseOutcome, OpenRolesBanner, RoleIntake
from dsm.models import Location, OpenRole, SkillDepth, SkillRequirement


def _role(role_id: str = "ROLE-Q1") -> OpenRole:
    return OpenRole(
        role_id=role_id,
        title="Backend Engineer",
        required_skills=[SkillRequirement(name="kotlin", depth=SkillDepth.HARD)],
        location=Location(city="Chennai"),
        co_location_required=True,
        start_date=date(2026, 7, 1),
    )


def test_banner_constructs() -> None:
    banner = OpenRolesBanner(demand_as_of=date(2026, 6, 15), source_path="data/raw/demand/x.csv")
    assert banner.demand_as_of == date(2026, 6, 15)
    assert banner.source_path == "data/raw/demand/x.csv"


def test_outcome_defaults_skipped_to_empty() -> None:
    outcome = DemandParseOutcome(
        banner=OpenRolesBanner(demand_as_of=date(2026, 6, 15), source_path="x.csv"),
        roles=[_role()],
    )
    assert outcome.skipped == []
    assert [r.role_id for r in outcome.roles] == ["ROLE-Q1"]


def test_models_are_frozen() -> None:
    banner = OpenRolesBanner(demand_as_of=date(2026, 6, 15), source_path="x.csv")
    with pytest.raises(ValidationError):
        banner.demand_as_of = date(2026, 6, 16)  # type: ignore[misc]
    outcome = DemandParseOutcome(banner=banner, roles=[])
    with pytest.raises(ValidationError):
        outcome.roles = [_role()]  # type: ignore[misc]


# --- RoleIntake (c-006 T-001) -------------------------------------------------------------


def test_role_intake_defaults_to_all_absent() -> None:
    """Every field defaults to null/empty so "absent ⇒ null, never guess" is representable."""
    intake = RoleIntake()
    assert intake.title is None
    assert intake.hard_skills == []
    assert intake.desired_skills == []
    assert intake.location_city is None
    assert intake.remote_within_country is False
    assert intake.start_date_iso is None
    assert intake.start_date_phrase is None
    assert intake.notes is None


def test_role_intake_has_no_co_location_field() -> None:
    """FR-8: co_location_required is Python-derived, NEVER an LLM output field (AD-002)."""
    assert "co_location_required" not in RoleIntake.model_fields


def test_role_intake_is_frozen() -> None:
    intake = RoleIntake(title="Backend Engineer")
    with pytest.raises(ValidationError):
        intake.title = "other"  # type: ignore[misc]


def test_role_intake_prompt_forbids_guessing() -> None:
    """FR-1-AC-3: the signature instruction must carry the verbatim never-guess directive."""
    prompt = load_prompt("role_intake")
    assert "leave any field absent from the text as null — never guess" in prompt.lower()
