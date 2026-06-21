"""Tests for the query-time demand intermediates (B-1 T-007; ee-query-architecture §6.1)."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from dsm.match.models import DemandParseOutcome, OpenRolesBanner
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
