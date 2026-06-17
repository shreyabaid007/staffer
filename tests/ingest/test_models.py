"""Instantiation tests for the ingest module-local contracts (T-001).

Verifies the shape required by I-SUM-1 and the OQ-5 decision (no ``IngestResult``
wrapper — the summary is its own type, returned beside the candidate dict).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dsm.ingest.models import IngestError, IngestSummary, RowIssue


def test_row_issue_instantiates() -> None:
    issue = RowIssue(
        sheet="New Joiners",
        row_number=4,
        email="x@paritypartners.example",
        reason="unparseable Join Date: '7th July'",
    )
    assert issue.sheet == "New Joiners"
    assert issue.row_number == 4
    assert issue.email == "x@paritypartners.example"


def test_row_issue_allows_missing_email() -> None:
    issue = RowIssue(sheet="Beach", row_number=9, email=None, reason="missing email")
    assert issue.email is None


def test_row_issue_is_frozen() -> None:
    issue = RowIssue(sheet="Beach", row_number=3, email=None, reason="r")
    with pytest.raises(ValidationError):
        issue.row_number = 5  # type: ignore[misc]


def test_ingest_summary_defaults_empty_issues() -> None:
    summary = IngestSummary(
        workbook_path="data/demand-supply.xlsx",
        candidate_rows_seen=35,
        candidates_ingested=35,
        blank_rows_skipped=0,
        duplicate_emails_skipped=0,
    )
    assert summary.issues == []
    assert summary.candidates_ingested == 35


def test_ingest_summary_carries_issues() -> None:
    issue = RowIssue(sheet="Beach", row_number=5, email=None, reason="missing email")
    summary = IngestSummary(
        workbook_path="wb.xlsx",
        candidate_rows_seen=2,
        candidates_ingested=1,
        blank_rows_skipped=0,
        duplicate_emails_skipped=0,
        issues=[issue],
    )
    assert summary.issues == [issue]


def test_ingest_summary_is_frozen() -> None:
    summary = IngestSummary(
        workbook_path="wb.xlsx",
        candidate_rows_seen=0,
        candidates_ingested=0,
        blank_rows_skipped=0,
        duplicate_emails_skipped=0,
    )
    with pytest.raises(ValidationError):
        summary.candidates_ingested = 1  # type: ignore[misc]


def test_ingest_error_is_exception() -> None:
    assert issubclass(IngestError, Exception)
    with pytest.raises(IngestError, match="missing tab"):
        raise IngestError("missing tab: Beach")
