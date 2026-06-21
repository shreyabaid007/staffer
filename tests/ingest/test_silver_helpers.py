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
    assert loc.remote_within_country is False
    assert loc.onsite_cities == frozenset()
    assert warnings == []


def test_parse_location_chennai_open_yes() -> None:
    """LOC-2: separate Chennai-open column → onsite_cities={'Chennai'} + warning (AD-086)."""
    loc, warnings = parse_location("Bengaluru", "Yes")
    assert loc.city == "Bengaluru"
    assert loc.onsite_cities == frozenset({"Chennai"})
    assert loc.remote_within_country is False
    assert any("Chennai-open" in w for w in warnings)


def test_parse_location_chennai_open_no_keeps_defaults() -> None:
    loc, _ = parse_location("Pune", "No")
    assert loc.remote_within_country is False
    assert loc.onsite_cities == frozenset()


def test_parse_location_remote_india() -> None:
    """LOC-3: Remote (India) → city None + remote_within_country + warning (AD-075/086)."""
    loc, warnings = parse_location("Remote (India)", "No")
    assert loc.city is None
    assert loc.remote_within_country is True
    assert loc.onsite_cities == frozenset()
    assert any("Remote (India)" in w for w in warnings)


def test_parse_location_remote_india_with_chennai_open() -> None:
    """AD-086: the two facets are orthogonal — Remote (India) AND Chennai-open carries both."""
    loc, _ = parse_location("Remote (India)", "Yes")
    assert loc.city is None
    assert loc.remote_within_country is True
    assert loc.onsite_cities == frozenset({"Chennai"})


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
