"""Tests for bronze-layer ingestion models (a-001 T-002)."""

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from dsm.ingest.models import (
    BronzeRecord,
    LandingStatus,
    ManifestEntry,
    RunManifest,
    SourceType,
)


def test_landed_entry_instantiates() -> None:
    entry = ManifestEntry(
        run_id="run-1",
        source_uri="data/raw/supply/beach.csv",
        source_type=SourceType.SUPPLY_BEACH,
        raw_bytes_hash="sha256:abc",
        size_bytes=123,
        discovered_at=datetime(2026, 6, 18, 12, 0, 0),
        snapshot_date=date(2026, 6, 1),
        status=LandingStatus.LANDED,
    )
    assert entry.source_type is SourceType.SUPPLY_BEACH
    assert entry.snapshot_date == date(2026, 6, 1)


def test_invalid_entry_allows_none_source_type_and_hash() -> None:
    """Unclassifiable/unreadable files have neither a source_type nor a hash."""
    entry = ManifestEntry(
        run_id="run-1",
        source_uri="data/raw/supply/notes.txt",
        source_type=None,
        raw_bytes_hash=None,
        size_bytes=10,
        discovered_at=datetime(2026, 6, 18, 12, 0, 0),
        status=LandingStatus.INVALID,
    )
    assert entry.source_type is None
    assert entry.raw_bytes_hash is None
    assert entry.snapshot_date is None  # defaults to None


def test_manifest_entry_is_frozen() -> None:
    entry = ManifestEntry(
        run_id="run-1",
        source_uri="x",
        source_type=SourceType.RESUME,
        raw_bytes_hash="sha256:abc",
        size_bytes=1,
        discovered_at=datetime(2026, 6, 18),
        status=LandingStatus.LANDED,
    )
    with pytest.raises(ValidationError):
        entry.status = LandingStatus.SKIPPED  # type: ignore[misc]


def test_bronze_record_accepts_str_and_list_values() -> None:
    csv_rec = BronzeRecord(
        source_hash="sha256:abc",
        source_type=SourceType.SUPPLY_BEACH,
        row_index=0,
        raw={"Name": "Aarav", "Days on Beach": "12"},
    )
    pdf_rec = BronzeRecord(
        source_hash="sha256:def",
        source_type=SourceType.RESUME,
        row_index=0,
        raw={"text": "...", "sections": ["SKILLS", "EXPERIENCE"], "email_found": "a@x.com"},
    )
    assert csv_rec.raw["Days on Beach"] == "12"
    assert pdf_rec.raw["sections"] == ["SKILLS", "EXPERIENCE"]


def test_bronze_record_is_frozen() -> None:
    rec = BronzeRecord(
        source_hash="sha256:abc",
        source_type=SourceType.FEEDBACK,
        row_index=2,
        raw={"email_key": "a@x.com", "raw_markdown": "great", "kind": "project"},
    )
    with pytest.raises(ValidationError):
        rec.row_index = 3  # type: ignore[misc]


def test_run_manifest_holds_entries_and_counts() -> None:
    entry = ManifestEntry(
        run_id="run-1",
        source_uri="x",
        source_type=SourceType.SUPPLY_BEACH,
        raw_bytes_hash="sha256:abc",
        size_bytes=1,
        discovered_at=datetime(2026, 6, 18),
        status=LandingStatus.LANDED,
    )
    manifest = RunManifest(run_id="run-1", entries=[entry], landed=1, skipped=0, invalid=0)
    assert manifest.landed == 1
    assert manifest.entries[0] is entry
