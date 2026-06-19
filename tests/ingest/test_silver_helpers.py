"""Tests for silver normalization helpers (a-002 T-005; GR-1, LOC-1..NET, AV-2)."""

from datetime import date

from dsm.ingest.models import Confidence, Grade
from dsm.ingest.silver import (
    coerce_confidence,
    parse_date,
    parse_grade,
    parse_location,
)


def test_parse_grade_known() -> None:
    assert parse_grade("Lead Consultant") == (Grade.LEAD_CONSULTANT, [])
    assert parse_grade("senior_consultant") == (Grade.SENIOR_CONSULTANT, [])


def test_parse_grade_missing_and_unknown() -> None:
    grade, warnings = parse_grade("   ")
    assert grade is None and warnings == ["grade missing"]
    grade, warnings = parse_grade("Wizard")
    assert grade is None and warnings and "Wizard" in warnings[0]


def test_parse_location_plain_city() -> None:
    loc, warnings = parse_location("Chennai", "No")
    assert loc.city == "Chennai"
    assert loc.remote_eligible is False
    assert warnings == []


def test_parse_location_chennai_open_yes() -> None:
    """LOC-2: separate Chennai-open column → remote_eligible + warning."""
    loc, warnings = parse_location("Bengaluru", "Yes")
    assert loc.city == "Bengaluru"
    assert loc.remote_eligible is True
    assert any("Chennai-open" in w for w in warnings)


def test_parse_location_chennai_open_no_keeps_remote_false() -> None:
    loc, _ = parse_location("Pune", "No")
    assert loc.remote_eligible is False


def test_parse_location_remote_india() -> None:
    """LOC-3: Remote (India) → city None + remote_eligible + warning (AD-075)."""
    loc, warnings = parse_location("Remote (India)", "No")
    assert loc.city is None
    assert loc.remote_eligible is True
    assert any("Remote (India)" in w for w in warnings)


def test_parse_location_remote_india_overrides_open_no() -> None:
    loc, _ = parse_location("Remote (India)", "Yes")
    assert loc.city is None and loc.remote_eligible is True


def test_parse_location_missing() -> None:
    loc, warnings = parse_location("", "No")
    assert loc.city is None
    assert "location missing" in warnings


def test_parse_date_formats() -> None:
    assert parse_date("2026-06-20") == date(2026, 6, 20)
    assert parse_date("20/06/2026") == date(2026, 6, 20)


def test_parse_date_blank_and_garbage() -> None:
    assert parse_date("") is None
    assert parse_date("not-a-date") is None


def test_coerce_confidence() -> None:
    assert coerce_confidence("High") == (Confidence.HIGH, [])
    assert coerce_confidence("med") == (Confidence.MEDIUM, [])
    conf, warnings = coerce_confidence("probably")
    assert conf is Confidence.LOW and warnings
