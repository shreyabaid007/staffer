"""Opt-in smoke test against real sample files in ``data/raw/`` (a-001).

Real files are PII-dense and **gitignored**, so this is NOT part of the default suite — it
skips unless ``DSM_REAL_SMOKE=1`` is set AND files are present. It also exercises the real
Docling PDF path (which loads model weights), so it is deliberately kept out of the offline
unit-test run (tech.md: no network/LLM in unit tests). Run it with ``make smoke``.

Unlike the golden-fixture tests, this asserts shape-level invariants (counts, presence) rather
than exact records, since the sample data may change.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dsm.ingest.blobstore import LocalFSBlobStore
from dsm.ingest.land import land
from dsm.ingest.lineage import build_run_manifest
from dsm.ingest.manifest import JSONLManifest
from dsm.ingest.models import LandingStatus, ManifestEntry, SourceType
from dsm.ingest.parse import parse_blob

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RAW = _REPO_ROOT / "data" / "raw"
_SUPPLY_TYPES = {
    SourceType.SUPPLY_BEACH,
    SourceType.SUPPLY_ROLLING_OFF,
    SourceType.SUPPLY_NEW_JOINERS,
}


def _real_files() -> list[Path]:
    if not _RAW.exists():
        return []
    return [p for p in _RAW.rglob("*") if p.is_file() and p.name != ".gitkeep"]


pytestmark = pytest.mark.skipif(
    os.environ.get("DSM_REAL_SMOKE") != "1" or not _real_files(),
    reason="opt-in: set DSM_REAL_SMOKE=1 with real files in data/raw/ (see `make smoke`)",
)


@pytest.fixture(scope="module")
def landed(tmp_path_factory: pytest.TempPathFactory):
    """Land all real files once, parse every landed blob from bronze."""
    bronze = tmp_path_factory.mktemp("bronze")
    blobs = LocalFSBlobStore(bronze)
    manifest = JSONLManifest(bronze / "manifest.jsonl")
    entries = land(_RAW, blobs, manifest, run_id="smoke")
    records: dict[str, list] = {}
    for e in entries:
        if e.source_type is not None and e.raw_bytes_hash is not None:
            records[e.source_uri] = parse_blob(
                blobs.get(e.raw_bytes_hash), e.source_type, e.raw_bytes_hash, run_id="smoke"
            )
    return entries, records, blobs, manifest


def test_all_files_land_with_no_invalid(landed) -> None:
    entries, _records, _blobs, _manifest = landed
    run = build_run_manifest("smoke", entries)
    assert run.invalid == 0, f"unexpected invalid files: {run.invalid}"
    assert run.landed == len(_real_files())
    assert run.skipped == 0


def test_every_landed_file_yields_records(landed) -> None:
    _entries, records, _blobs, _manifest = landed
    for uri, recs in records.items():
        assert len(recs) >= 1, f"{uri} produced no bronze records"


def test_supply_csvs_get_snapshot_date(landed) -> None:
    entries, _records, _blobs, _manifest = landed
    supply = [e for e in entries if e.source_type in _SUPPLY_TYPES]
    assert supply, "no supply CSVs found in sample data"
    for e in supply:
        assert e.snapshot_date is not None, f"{e.source_uri} missing as-of snapshot_date"


def test_resume_records_carry_an_email(landed) -> None:
    _entries, records, _blobs, _manifest = landed
    resume_recs = [
        r for recs in records.values() for r in recs if r.source_type is SourceType.RESUME
    ]
    assert resume_recs, "no resume records found in sample data"
    for r in resume_recs:
        assert r.raw["email_found"], f"resume {r.source_hash} has no email_found"


def test_feedback_records_carry_an_email_key(landed) -> None:
    _entries, records, _blobs, _manifest = landed
    fb_recs = [
        r for recs in records.values() for r in recs if r.source_type is SourceType.FEEDBACK
    ]
    assert fb_recs, "no feedback records found in sample data"
    for r in fb_recs:
        assert r.raw["email_key"], f"feedback item {r.row_index} has no email_key"


def test_reland_is_idempotent(landed) -> None:
    entries, _records, blobs, manifest = landed
    second: list[ManifestEntry] = land(_RAW, blobs, manifest, run_id="smoke-2")
    assert {e.status for e in second} == {LandingStatus.SKIPPED}
