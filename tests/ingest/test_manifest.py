"""Tests for the JSONL landing manifest (a-001 T-004)."""

from datetime import date, datetime
from pathlib import Path

from dsm.ingest.manifest import JSONLManifest
from dsm.ingest.models import LandingStatus, ManifestEntry, SourceType


def _entry(
    *,
    run_id: str = "run-1",
    raw_bytes_hash: str | None = "sha256:abc",
    status: LandingStatus = LandingStatus.LANDED,
    source_type: SourceType | None = SourceType.SUPPLY_BEACH,
    snapshot_date: date | None = None,
) -> ManifestEntry:
    return ManifestEntry(
        run_id=run_id,
        source_uri="data/raw/supply/beach.csv",
        source_type=source_type,
        raw_bytes_hash=raw_bytes_hash,
        size_bytes=1,
        discovered_at=datetime(2026, 6, 18, 12, 0, 0),
        snapshot_date=snapshot_date,
        status=status,
    )


def test_append_then_read_back_round_trips(tmp_path: Path) -> None:
    m = JSONLManifest(tmp_path / "manifest.jsonl")
    e = _entry(snapshot_date=date(2026, 6, 1))
    m.append(e)
    assert m.entries_for_run("run-1") == [e]


def test_has_hash_true_only_for_landed(tmp_path: Path) -> None:
    m = JSONLManifest(tmp_path / "manifest.jsonl")
    m.append(_entry(raw_bytes_hash="sha256:landed", status=LandingStatus.LANDED))
    m.append(_entry(raw_bytes_hash="sha256:skipped", status=LandingStatus.SKIPPED))
    m.append(
        _entry(
            raw_bytes_hash=None,
            status=LandingStatus.INVALID,
            source_type=None,
        )
    )
    assert m.has_hash("sha256:landed") is True
    assert m.has_hash("sha256:skipped") is False  # SKIPPED is not a dedup authority
    assert m.has_hash("sha256:never") is False


def test_has_hash_on_missing_file_is_false(tmp_path: Path) -> None:
    m = JSONLManifest(tmp_path / "manifest.jsonl")
    assert m.has_hash("sha256:anything") is False


def test_entries_for_run_filters_and_preserves_order(tmp_path: Path) -> None:
    m = JSONLManifest(tmp_path / "manifest.jsonl")
    a1 = _entry(run_id="run-A", raw_bytes_hash="sha256:1")
    b1 = _entry(run_id="run-B", raw_bytes_hash="sha256:2")
    a2 = _entry(run_id="run-A", raw_bytes_hash="sha256:3")
    for e in (a1, b1, a2):
        m.append(e)
    assert m.entries_for_run("run-A") == [a1, a2]
    assert m.entries_for_run("run-B") == [b1]


def test_invalid_entry_with_none_fields_round_trips(tmp_path: Path) -> None:
    m = JSONLManifest(tmp_path / "manifest.jsonl")
    e = _entry(raw_bytes_hash=None, source_type=None, status=LandingStatus.INVALID)
    m.append(e)
    assert m.entries_for_run("run-1") == [e]
