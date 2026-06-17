"""Unit tests for the pure parsing helpers (T-002).

Covers I-LOAD-3 (header resolution by name), I-CAND-3 (skill normalisation),
I-CAND-4 (location + Chennai-open / remote), and I-EDGE-3 (date coercion). No
network, no LLM — synthetic in-memory values and an openpyxl ``Workbook()``.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from openpyxl import Workbook

from dsm.ingest.models import IngestError
from dsm.ingest.sheets import (
    _header_index,
    _is_blank,
    parse_date,
    parse_location,
    parse_skills,
)
from dsm.models import ProficiencyLevel


# --- parse_date (I-EDGE-3) -------------------------------------------------
def test_parse_date_accepts_date() -> None:
    assert parse_date(date(2026, 6, 22)) == date(2026, 6, 22)


def test_parse_date_accepts_datetime() -> None:
    assert parse_date(datetime(2026, 6, 22, 9, 30)) == date(2026, 6, 22)


def test_parse_date_accepts_iso_string() -> None:
    assert parse_date("2026-06-22") == date(2026, 6, 22)
    assert parse_date("  2026-06-22  ") == date(2026, 6, 22)


@pytest.mark.parametrize("bad", ["7th July", "22-06-2026", "", None, 42, 3.14])
def test_parse_date_rejects_garbage(bad: object) -> None:
    with pytest.raises(ValueError):
        parse_date(bad)


# --- parse_skills (I-CAND-3) -----------------------------------------------
def test_parse_skills_normalises_trims_dedupes() -> None:
    skills = parse_skills("Java, Kotlin , java")
    assert [s.name for s in skills] == ["java", "kotlin"]
    assert all(s.proficiency is ProficiencyLevel.INTERMEDIATE for s in skills)


def test_parse_skills_preserves_first_seen_order() -> None:
    skills = parse_skills("Kotlin, Java, Spring Boot, AWS")
    assert [s.name for s in skills] == ["kotlin", "java", "spring boot", "aws"]


@pytest.mark.parametrize("empty", [None, "", "   ", ",, ,"])
def test_parse_skills_empty_is_empty_list(empty: object) -> None:
    assert parse_skills(empty) == []


# --- parse_location (I-CAND-4) ---------------------------------------------
def test_parse_location_slash_remote_segment() -> None:
    loc = parse_location("Bengaluru / remote-India", None)
    assert loc.city == "Bengaluru"
    assert loc.remote_eligible is True
    assert loc.country == "India"


def test_parse_location_chennai_open_no() -> None:
    loc = parse_location("Chennai", "No")
    assert loc.city == "Chennai"
    assert loc.remote_eligible is False


def test_parse_location_chennai_open_yes() -> None:
    loc = parse_location("Chennai", "Yes")
    assert loc.city == "Chennai"
    assert loc.remote_eligible is True


def test_parse_location_remote_label_all_remote() -> None:
    loc = parse_location("Remote (India)", "No")
    assert loc.remote_eligible is True
    assert loc.city == "Remote (India)"


# --- _header_index (I-LOAD-2/3) --------------------------------------------
def _ws_with_headers(headers: list[str]):
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["Title row"])  # row 1 is the title
    ws.append(headers)  # row 2 is the header row
    return ws


def test_header_index_resolves_by_name_not_position() -> None:
    ws = _ws_with_headers(["#", "Name", "Email", "Key Skills", "Location"])
    headers = _header_index(ws, sheet="Beach", required=["Email", "Name"])
    assert headers["name"] == 2
    assert headers["email"] == 3
    assert headers["key skills"] == 4


def test_header_index_missing_required_raises() -> None:
    ws = _ws_with_headers(["#", "Name", "Location"])  # no Email
    with pytest.raises(IngestError, match="Email"):
        _header_index(ws, sheet="Beach", required=["Name", "Email"])


# --- _is_blank (I-EDGE-2) --------------------------------------------------
def test_is_blank_all_empty() -> None:
    assert _is_blank([None, "", "   ", None]) is True


def test_is_blank_with_value() -> None:
    assert _is_blank([None, "", "x"]) is False
