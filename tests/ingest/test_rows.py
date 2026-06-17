"""Unit tests for supply-row → ``Candidate`` mapping (T-003).

Covers I-CAND-1 (field mapping), I-CAND-2 (availability = sheet membership),
I-CAND-5 (confidence carry-through), I-CAND-6 (new-joiner ``source``). Rows are
built directly from header maps + value tuples — no workbook, no I/O.
"""

from __future__ import annotations

from datetime import date

import pytest

from dsm.ingest.sheets import _row_to_candidate
from dsm.models import (
    CandidateSource,
    FreeNow,
    NewJoiner,
    RollingOff,
)


def _headers(names: list[str]) -> dict[str, int]:
    return {name.strip().lower(): i + 1 for i, name in enumerate(names)}


BEACH_HEADERS = _headers(
    [
        "#",
        "Name",
        "Email",
        "Grade",
        "Key Skills",
        "Location",
        "Chennai-open",
        "Days on Beach",
        "Notes",
    ]
)
ROLLING_HEADERS = _headers(
    [
        "#",
        "Name",
        "Email",
        "Grade",
        "Key Skills",
        "Current Client",
        "Roll-off Date",
        "Confidence",
        "Location",
        "Chennai-open",
        "Notes",
    ]
)
JOINER_HEADERS = _headers(
    [
        "#",
        "Name",
        "Email",
        "Grade",
        "Key Skills (from CV)",
        "Join Date",
        "Location",
        "Chennai-open",
        "Notes",
    ]
)


def test_beach_row_is_free_now() -> None:
    values = [
        1.0,
        "Karan Mehta",
        "karan@x.example",
        "Lead",
        "Java, Kotlin",
        "Bengaluru",
        "Yes",
        37.0,
        "n",
    ]
    cand = _row_to_candidate(values, BEACH_HEADERS, CandidateSource.BEACH)
    assert cand.email == "karan@x.example"
    assert cand.name == "Karan Mehta"
    assert isinstance(cand.availability, FreeNow)
    assert cand.source is CandidateSource.BEACH
    assert [s.name for s in cand.skills] == ["java", "kotlin"]
    assert cand.location.city == "Bengaluru"
    assert cand.location.remote_eligible is True  # Chennai-open == Yes
    assert cand.feedback.entries == []


def test_rolling_off_row_carries_confidence() -> None:
    values = [
        1.0,
        "Aarav",
        "aarav@x.example",
        "Lead",
        "Kotlin, Java",
        "Meridian Pay",
        "2026-08-18",
        "low",
        "Bengaluru",
        "No",
        "n",
    ]
    cand = _row_to_candidate(values, ROLLING_HEADERS, CandidateSource.ROLLING_OFF)
    assert isinstance(cand.availability, RollingOff)
    assert cand.availability.expected_date == date(2026, 8, 18)
    assert cand.availability.confidence == "low"
    assert cand.source is CandidateSource.ROLLING_OFF


def test_rolling_off_confidence_is_case_normalised() -> None:
    values = [
        1.0,
        "X",
        "x@x.example",
        "Lead",
        "Java",
        "Client",
        "2026-07-01",
        "HIGH",
        "Chennai",
        "No",
        "n",
    ]
    cand = _row_to_candidate(values, ROLLING_HEADERS, CandidateSource.ROLLING_OFF)
    assert isinstance(cand.availability, RollingOff)
    assert cand.availability.confidence == "high"


def test_rolling_off_bad_confidence_raises() -> None:
    values = [
        1.0,
        "X",
        "x@x.example",
        "Lead",
        "Java",
        "Client",
        "2026-07-01",
        "maybe",
        "Chennai",
        "No",
        "n",
    ]
    with pytest.raises(ValueError, match="confidence"):
        _row_to_candidate(values, ROLLING_HEADERS, CandidateSource.ROLLING_OFF)


def test_new_joiner_row_sets_source_and_join_date() -> None:
    values = [
        1.0,
        "Vikram",
        "vikram@x.example",
        "Senior",
        "Kotlin, AWS",
        "2026-06-25",
        "Bengaluru",
        "Yes",
        "n",
    ]
    cand = _row_to_candidate(values, JOINER_HEADERS, CandidateSource.NEW_JOINER)
    assert isinstance(cand.availability, NewJoiner)
    assert cand.availability.join_date == date(2026, 6, 25)
    # OQ-1: new-joiner-ness is carried by source (no is_unverified on the frozen model).
    assert cand.source is CandidateSource.NEW_JOINER


def test_new_joiner_reads_key_skills_from_cv_column() -> None:
    values = [
        1.0,
        "Aisha",
        "aisha@x.example",
        "Senior",
        "Python, RAG",
        "2026-07-11",
        "Remote (India)",
        "No",
        "n",
    ]
    cand = _row_to_candidate(values, JOINER_HEADERS, CandidateSource.NEW_JOINER)
    assert [s.name for s in cand.skills] == ["python", "rag"]
    assert cand.location.remote_eligible is True


def test_missing_email_raises() -> None:
    values = [1.0, "NoEmail", None, "Lead", "Java", "Bengaluru", "Yes", 5.0, "n"]
    with pytest.raises(ValueError, match="email"):
        _row_to_candidate(values, BEACH_HEADERS, CandidateSource.BEACH)


def test_missing_name_raises() -> None:
    values = [1.0, "", "x@x.example", "Lead", "Java", "Bengaluru", "Yes", 5.0, "n"]
    with pytest.raises(ValueError, match="name"):
        _row_to_candidate(values, BEACH_HEADERS, CandidateSource.BEACH)


def test_new_joiner_bad_date_raises() -> None:
    values = [1.0, "X", "x@x.example", "Senior", "Java", "7th July", "Bengaluru", "Yes", "n"]
    with pytest.raises(ValueError, match="date"):
        _row_to_candidate(values, JOINER_HEADERS, CandidateSource.NEW_JOINER)
