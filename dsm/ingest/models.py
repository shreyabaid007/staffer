"""Module-local contracts for candidate sheet ingestion (a-001-ingest-sheets).

Shared domain models live in ``dsm/models.py`` (frozen, AD-060). These types are
local to the ingest phase: the per-row issue record and the run summary returned
alongside the candidate map, plus the fatal structural error.

Per OQ-5 (signed off) there is **no ``IngestResult`` wrapper**: ``ingest_candidates``
returns ``tuple[dict[str, Candidate], IngestSummary]`` directly.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RowIssue(BaseModel, frozen=True):
    """A single supply row that could not be ingested (I-VAL-1, I-EDGE-1/3).

    Recorded — never silently dropped (``docs/tech.md`` §Coding standards).
    """

    sheet: str  # tab name, e.g. "Beach"
    row_number: int  # 1-based spreadsheet row (matches what a human sees in Excel)
    email: str | None  # the row's email if it could be read, else None
    reason: str  # human-readable, e.g. "unparseable Join Date: '7th July'"


class IngestSummary(BaseModel, frozen=True):
    """What a single ``ingest_candidates`` run loaded and what it skipped (I-SUM-1)."""

    workbook_path: str
    candidate_rows_seen: int
    candidates_ingested: int
    blank_rows_skipped: int
    duplicate_emails_skipped: int
    issues: list[RowIssue] = Field(default_factory=list)


class IngestError(Exception):
    """Fatal structural failure: a supply tab or required header is missing (I-LOAD-2).

    Distinct from a per-row ``RowIssue`` — a malformed workbook is not recoverable
    row-by-row, so the read aborts.
    """
