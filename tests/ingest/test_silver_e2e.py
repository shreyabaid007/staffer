"""End-to-end land → parse → silver over committed golden fixtures (a-002 T-008).

Library-level companion to the CLI test: drives the real bronze→silver path over synthetic
real-shaped supply CSVs and asserts the resulting NormalizedRecords. Offline, deterministic.
"""

import shutil
from datetime import date
from pathlib import Path

from dsm.ingest.blobstore import LocalFSBlobStore
from dsm.ingest.land import land
from dsm.ingest.manifest import JSONLManifest
from dsm.ingest.models import NormalizedRecord, SourceType
from dsm.ingest.parse import parse_blob
from dsm.ingest.silver import normalize_run, read_normalized, write_normalized
from dsm.ingest.taxonomy import load_taxonomy

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ingest" / "silver"


def _stage_raw(tmp_path: Path) -> Path:
    raw = tmp_path / "raw" / "supply"
    raw.mkdir(parents=True)
    shutil.copy(_FIXTURES / "beach.csv", raw / "beach.csv")
    shutil.copy(_FIXTURES / "new_joiners.csv", raw / "new_joiners.csv")
    return tmp_path / "raw"


def _run_silver(tmp_path: Path) -> list[NormalizedRecord]:
    raw = _stage_raw(tmp_path)
    bronze = tmp_path / "bronze"
    silver = tmp_path / "silver"
    blobs = LocalFSBlobStore(bronze)
    manifest = JSONLManifest(bronze / "manifest.jsonl")
    entries = land(raw, blobs, manifest, run_id="run-e2e")
    taxonomy = load_taxonomy()

    out: list[NormalizedRecord] = []
    for entry in entries:
        assert entry.raw_bytes_hash is not None and entry.source_type is not None
        records = parse_blob(
            blobs.get(entry.raw_bytes_hash),
            entry.source_type,
            entry.raw_bytes_hash,
            run_id="run-e2e",
        )
        normalized = normalize_run(
            records,
            snapshot_dates={entry.raw_bytes_hash: entry.snapshot_date},
            taxonomy=taxonomy,
            run_id="run-e2e",
        )
        write_normalized(normalized, entry.raw_bytes_hash, silver)
        out.extend(normalized)
    return out


def test_bronze_to_silver_over_fixtures(tmp_path: Path) -> None:
    records = _run_silver(tmp_path)
    by_type: dict[SourceType, list[NormalizedRecord]] = {}
    for record in records:
        by_type.setdefault(record.source_type, []).append(record)

    beach = by_type[SourceType.SUPPLY_BEACH]
    joiners = by_type[SourceType.SUPPLY_NEW_JOINERS]
    assert len(beach) == 2
    assert len(joiners) == 1

    # valid_as_of stamped from the banner (VAOF-1).
    assert all(r.valid_as_of == date(2026, 6, 1) for r in records)

    # Beach → FreeNow; Priya is Chennai-open → remote_eligible + warning (LOC-2).
    priya = next(
        r
        for r in beach
        if r.availability
        and r.availability.type == "free_now"
        and r.location
        and r.location.remote_eligible
    )
    assert any("Chennai-open" in w for w in priya.parse_warnings)

    # New joiner → NewJoiner, Remote (India) → city None + remote_eligible (LOC-3),
    # CV skills unverified (AD-032), Cobol unmapped (TX-2).
    nadia = joiners[0]
    assert nadia.availability is not None and nadia.availability.type == "new_joiner"
    assert (
        nadia.location is not None
        and nadia.location.city is None
        and nadia.location.remote_eligible
    )
    assert nadia.skills and all(s.unverified for s in nadia.skills)
    assert any(s.unmapped and s.name == "Cobol" for s in nadia.skills)


def test_candidate_id_is_stable_across_reruns(tmp_path: Path) -> None:
    first = {r.candidate_id for r in _run_silver(tmp_path)}
    second = {r.candidate_id for r in _run_silver(tmp_path / "again")}
    assert first == second
    assert all(cid.startswith("cid:") for cid in first)


def test_silver_layer_round_trips_from_disk(tmp_path: Path) -> None:
    records = _run_silver(tmp_path)
    for record in records:
        on_disk = read_normalized(record.source_hash, tmp_path / "silver")
        assert record in on_disk
