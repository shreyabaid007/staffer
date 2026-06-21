"""Tests for the demand CSV parser (B-1 T-009; FR-1; §6.1).

Deterministic, offline: each test writes a small Open Roles CSV to ``tmp_path`` and asserts the
parsed ``DemandParseOutcome``. Covers the banner, the two skill encodings, co-location mapping,
Notes→description, Priority ordering, malformed-row skip/count, and the missing-banner block.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from pathlib import Path

import pytest

from dsm.match.demand import parse_demand
from dsm.models import ProficiencyLevel, SkillDepth

_HEADER = [
    "Role ID",
    "Title",
    "Client",
    "Sector",
    "Required Skills",
    "Start",
    "Location",
    "Co-location",
    "Priority",
    "Notes / Constraints",
]


def _row(
    *,
    role_id: str = "ROLE-01",
    title: str = "Backend Engineer",
    skills: str = "kotlin (expert)",
    start: str = "2026-07-01",
    location: str = "Chennai",
    co_location: str = "Yes",
    priority: str = "1",
    notes: str = "",
) -> list[str]:
    return [
        role_id,
        title,
        "Acme",
        "Fintech",
        skills,
        start,
        location,
        co_location,
        priority,
        notes,
    ]


def _write_csv(
    tmp_path: Path,
    rows: list[list[str]],
    *,
    banner: str = "Open Roles - Acme - as of 15 Jun 2026",
    header: list[str] | None = None,
) -> Path:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header if header is not None else _HEADER)
    for row in rows:
        writer.writerow(row)
    path = tmp_path / "open_roles.csv"
    text = (banner + "\n" if banner else "") + buf.getvalue()
    path.write_text(text, encoding="utf-8")
    return path


def test_fr_1_ac_1_banner_parsed_to_demand_as_of(tmp_path: Path) -> None:
    outcome = parse_demand(_write_csv(tmp_path, [_row()]))
    assert outcome.banner.demand_as_of == date(2026, 6, 15)
    assert outcome.banner.source_path.endswith("open_roles.csv")


def test_fr_1_ac_2_proficiency_qualifier_is_hard(tmp_path: Path) -> None:
    outcome = parse_demand(_write_csv(tmp_path, [_row(skills="kotlin (expert)")]))
    skill = outcome.roles[0].required_skills[0]
    assert skill.name == "kotlin"
    assert skill.depth is SkillDepth.HARD
    assert skill.min_proficiency is ProficiencyLevel.EXPERT


@pytest.mark.parametrize(
    "qualifier,level",
    [
        ("beginner", ProficiencyLevel.BEGINNER),
        ("intermediate", ProficiencyLevel.INTERMEDIATE),
        ("advanced", ProficiencyLevel.ADVANCED),
        ("expert", ProficiencyLevel.EXPERT),
    ],
)
def test_all_proficiency_words_map_to_hard(
    tmp_path: Path, qualifier: str, level: ProficiencyLevel
) -> None:
    outcome = parse_demand(_write_csv(tmp_path, [_row(skills=f"python ({qualifier})")]))
    skill = outcome.roles[0].required_skills[0]
    assert skill.depth is SkillDepth.HARD
    assert skill.min_proficiency is level


def test_fr_1_ac_3_bare_skill_is_desired(tmp_path: Path) -> None:
    outcome = parse_demand(_write_csv(tmp_path, [_row(skills="kotlin (expert); aws")]))
    aws = next(s for s in outcome.roles[0].required_skills if s.name == "aws")
    assert aws.depth is SkillDepth.DESIRED
    assert aws.min_proficiency is None


def test_fr_1_ac_4_nice_to_have_is_desired(tmp_path: Path) -> None:
    outcome = parse_demand(
        _write_csv(tmp_path, [_row(skills="kotlin (expert); kafka (nice to have)")])
    )
    kafka = next(s for s in outcome.roles[0].required_skills if s.name == "kafka")
    assert kafka.depth is SkillDepth.DESIRED
    assert kafka.min_proficiency is None


def test_unknown_qualifier_defaults_to_desired(tmp_path: Path) -> None:
    """An unrecognised parenthetical (not a proficiency word) → DESIRED, name stripped of it."""
    outcome = parse_demand(_write_csv(tmp_path, [_row(skills="redis (preferred)")]))
    redis = outcome.roles[0].required_skills[0]
    assert redis.name == "redis"
    assert redis.depth is SkillDepth.DESIRED
    assert redis.min_proficiency is None


def test_skill_split_preserves_all_tokens(tmp_path: Path) -> None:
    outcome = parse_demand(
        _write_csv(tmp_path, [_row(skills="kotlin (expert); kafka (nice to have); aws")])
    )
    skills = outcome.roles[0].required_skills
    assert {s.name for s in skills} == {"kotlin", "kafka", "aws"}
    assert sum(1 for s in skills if s.depth is SkillDepth.HARD) == 1


def test_co_location_mapping(tmp_path: Path) -> None:
    yes_dir = tmp_path / "y"
    no_dir = tmp_path / "n"
    yes_dir.mkdir()
    no_dir.mkdir()
    yes = parse_demand(_write_csv(yes_dir, [_row(co_location="Yes")]))
    no = parse_demand(_write_csv(no_dir, [_row(co_location="No")]))
    assert yes.roles[0].co_location_required is True
    assert no.roles[0].co_location_required is False


def test_fr_1_ac_5_notes_map_to_description_verbatim(tmp_path: Path) -> None:
    note = "Must have led a payments platform; no relocation budget."
    outcome = parse_demand(_write_csv(tmp_path, [_row(notes=note)]))
    assert outcome.roles[0].description == note


def test_blank_notes_become_none(tmp_path: Path) -> None:
    outcome = parse_demand(_write_csv(tmp_path, [_row(notes="")]))
    assert outcome.roles[0].description is None


def test_remote_india_location(tmp_path: Path) -> None:
    outcome = parse_demand(
        _write_csv(tmp_path, [_row(location="Remote (India)", co_location="No")])
    )
    loc = outcome.roles[0].location
    assert loc.city is None
    assert loc.remote_within_country is True
    assert loc.onsite_cities == frozenset()


def test_plain_city_location(tmp_path: Path) -> None:
    outcome = parse_demand(_write_csv(tmp_path, [_row(location="Chennai")]))
    loc = outcome.roles[0].location
    assert loc.city == "Chennai"
    assert loc.remote_within_country is False


def test_fr_1_ac_8_batch_ordered_by_priority(tmp_path: Path) -> None:
    rows = [
        _row(role_id="ROLE-C", priority="3"),
        _row(role_id="ROLE-A", priority="1"),
        _row(role_id="ROLE-B", priority="2"),
    ]
    outcome = parse_demand(_write_csv(tmp_path, rows))
    assert [r.role_id for r in outcome.roles] == ["ROLE-A", "ROLE-B", "ROLE-C"]


def test_priority_ties_break_on_role_id(tmp_path: Path) -> None:
    rows = [
        _row(role_id="ROLE-Z", priority="1"),
        _row(role_id="ROLE-A", priority="1"),
    ]
    outcome = parse_demand(_write_csv(tmp_path, rows))
    assert [r.role_id for r in outcome.roles] == ["ROLE-A", "ROLE-Z"]


def test_fr_1_ac_6_malformed_rows_skipped_and_counted(tmp_path: Path) -> None:
    rows = [
        _row(role_id="ROLE-OK", priority="1"),
        _row(role_id="", priority="2"),  # missing Role ID
        _row(role_id="ROLE-BADSTART", start="not-a-date", priority="3"),
        _row(role_id="ROLE-NOSKILL", skills="", priority="4"),
    ]
    outcome = parse_demand(_write_csv(tmp_path, rows))
    assert [r.role_id for r in outcome.roles] == ["ROLE-OK"]
    assert len(outcome.skipped) == 3
    blob = " ".join(outcome.skipped)
    assert "missing Role ID" in blob
    assert "unparseable Start" in blob
    assert "empty Required Skills" in blob


def test_column_count_mismatch_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "open_roles.csv"
    path.write_text(
        "Open Roles - Acme - as of 15 Jun 2026\n"
        + ",".join(_HEADER)
        + "\n"
        + "ROLE-SHORT,only,three\n"  # fewer columns than the header
        + "ROLE-OK,Eng,Acme,Fintech,kotlin (expert),2026-07-01,Chennai,Yes,1,\n",
        encoding="utf-8",
    )
    outcome = parse_demand(path)
    assert [r.role_id for r in outcome.roles] == ["ROLE-OK"]
    assert any("column-count mismatch" in s for s in outcome.skipped)


def test_fr_1_ac_7_missing_banner_blocks(tmp_path: Path) -> None:
    path = _write_csv(tmp_path, [_row()], banner="")  # no banner line
    with pytest.raises(ValueError, match="banner"):
        parse_demand(path)


def test_unparseable_banner_date_blocks(tmp_path: Path) -> None:
    path = _write_csv(tmp_path, [_row()], banner="Open Roles - Acme - as of someday")
    with pytest.raises(ValueError, match="banner"):
        parse_demand(path)


def test_empty_csv_with_banner_is_not_a_block(tmp_path: Path) -> None:
    """Edge case (design §5): banner present, no data rows → empty roles, no skips, no raise."""
    outcome = parse_demand(_write_csv(tmp_path, []))
    assert outcome.roles == []
    assert outcome.skipped == []
    assert outcome.banner.demand_as_of == date(2026, 6, 15)
