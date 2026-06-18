"""End-to-end landing → parse over a synthetic raw dir (a-001 T-010).

Offline and deterministic: the Docling conversion is monkeypatched (NF-NONET-1). Exercises the
whole bronze foundation — discover, classify, hash, dedup, write, parse, and run-manifest
tallies — plus an idempotent re-land.
"""

from datetime import date
from pathlib import Path

import dsm.ingest.parse.pdf as pdf_mod
from dsm.ingest.blobstore import LocalFSBlobStore, read_records, write_records
from dsm.ingest.land import land
from dsm.ingest.lineage import build_run_manifest
from dsm.ingest.manifest import JSONLManifest
from dsm.ingest.models import LandingStatus, SourceType
from dsm.ingest.parse import parse_blob


def _build_raw(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    for sub in ("supply", "resumes", "feedback"):
        (raw / sub).mkdir(parents=True)
    (raw / "supply" / "beach.csv").write_bytes(
        b"as of 2026-06-01\nName,City\nAarav,Chennai\nVikram,Pune\n"
    )
    (raw / "resumes" / "aarav.pdf").write_bytes(b"%PDF-fake-bytes")
    (raw / "feedback" / "aarav.md").write_bytes(
        b"email: aarav@example.com\n\n## Project Review\nStrong delivery.\n"
    )
    return raw


def test_land_then_parse_all_sources(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        pdf_mod,
        "_extract",
        lambda data, *, ocr: ("Aarav resume, aarav@example.com", ["SKILLS"]),
    )
    raw = _build_raw(tmp_path)
    blobs = LocalFSBlobStore(tmp_path / "bronze")
    manifest = JSONLManifest(tmp_path / "bronze" / "manifest.jsonl")

    entries = land(raw, blobs, manifest, run_id="run-1")
    run = build_run_manifest("run-1", entries)
    assert (run.landed, run.skipped, run.invalid) == (3, 0, 0)

    # Supply CSV banner is stamped on the manifest entry (LAND-ASOF-1).
    csv_entry = next(e for e in entries if e.source_type is SourceType.SUPPLY_BEACH)
    assert csv_entry.snapshot_date == date(2026, 6, 1)

    # Parse every landed blob from bronze (replay from bronze, not the source)
    # and persist records to records/<hash>.jsonl (L-LAYOUT-2).
    bronze_root = tmp_path / "bronze"
    records_by_type: dict[SourceType, list] = {}
    for entry in entries:
        assert entry.raw_bytes_hash is not None and entry.source_type is not None
        data = blobs.get(entry.raw_bytes_hash)
        records = parse_blob(data, entry.source_type, entry.raw_bytes_hash, run_id="run-1")
        write_records(records, entry.raw_bytes_hash, bronze_root)
        records_by_type[entry.source_type] = records

    assert [r.raw["Name"] for r in records_by_type[SourceType.SUPPLY_BEACH]] == ["Aarav", "Vikram"]
    assert records_by_type[SourceType.RESUME][0].raw["email_found"] == "aarav@example.com"
    assert records_by_type[SourceType.FEEDBACK][0].raw["email_key"] == "aarav@example.com"

    # Verify records survive a round-trip from disk (L-LAYOUT-2).
    for entry in entries:
        assert entry.raw_bytes_hash is not None
        assert entry.source_type is not None
        loaded = read_records(entry.raw_bytes_hash, bronze_root)
        assert len(loaded) > 0
        assert loaded == records_by_type[entry.source_type]


def test_reland_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pdf_mod, "_extract", lambda data, *, ocr: ("resume text", []))
    raw = _build_raw(tmp_path)
    blobs = LocalFSBlobStore(tmp_path / "bronze")
    manifest = JSONLManifest(tmp_path / "bronze" / "manifest.jsonl")

    land(raw, blobs, manifest, run_id="run-1")
    second = land(raw, blobs, manifest, run_id="run-2")
    run = build_run_manifest("run-2", second)

    assert {e.status for e in second} == {LandingStatus.SKIPPED}
    assert (run.landed, run.skipped, run.invalid) == (0, 3, 0)
