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


# ---------------------------------------------------------------------------
# Phase 3 — silver models (a-002 T-002)
# ---------------------------------------------------------------------------


def test_normalized_skill_defaults() -> None:
    from dsm.ingest.models import NormalizedSkill

    skill = NormalizedSkill(name="kotlin")
    assert skill.proficiency is None
    assert skill.unmapped is False
    assert skill.unverified is False


def test_normalized_record_holds_frozen_availability() -> None:
    from datetime import date as _date

    from dsm.ingest.models import (
        Confidence,
        Grade,
        NormalizedRecord,
        NormalizedSkill,
    )
    from dsm.models import Location, RollingOff

    rec = NormalizedRecord(
        candidate_id="cid:abc",
        source_type=SourceType.SUPPLY_ROLLING_OFF,
        source_hash="sha256:def",
        valid_as_of=_date(2026, 6, 1),
        grade=Grade.LEAD_CONSULTANT,
        location=Location(city="Pune", remote_within_country=True),
        availability=RollingOff(
            expected_date=_date(2026, 6, 20), confidence=Confidence.MEDIUM.value
        ),
        skills=[NormalizedSkill(name="java", unverified=True)],
        extractor_version="silver-v1",
    )
    assert rec.candidate_id == "cid:abc"
    assert isinstance(rec.availability, RollingOff)
    assert rec.availability.confidence == "medium"
    assert rec.skills[0].unverified is True


def test_normalized_record_is_frozen() -> None:
    from dsm.ingest.models import NormalizedRecord

    rec = NormalizedRecord(
        candidate_id="cid:abc",
        source_type=SourceType.RESUME,
        source_hash="sha256:def",
        extractor_version="silver-v1",
    )
    with pytest.raises(ValidationError):
        rec.candidate_id = "cid:other"  # type: ignore[misc]


def test_normalized_record_round_trips_via_json() -> None:
    from datetime import date as _date

    from dsm.ingest.models import NormalizedRecord
    from dsm.models import FreeNow, Location

    rec = NormalizedRecord(
        candidate_id="cid:abc",
        source_type=SourceType.SUPPLY_BEACH,
        source_hash="sha256:def",
        valid_as_of=_date(2026, 6, 1),
        location=Location(city="Chennai"),
        availability=FreeNow(),
        extractor_version="silver-v1",
    )
    restored = NormalizedRecord.model_validate_json(rec.model_dump_json())
    assert restored == rec
    assert isinstance(restored.availability, FreeNow)
