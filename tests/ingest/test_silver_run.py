"""Tests for normalize_run: valid_as_of threading, determinism, no-merge (a-002 T-007)."""

from datetime import date

from dsm.ingest.models import BronzeRecord, SourceType
from dsm.ingest.silver import normalize_run
from dsm.ingest.taxonomy import Taxonomy

_TAX = Taxonomy({"java": "java"})


def _beach(email: str, source_hash: str) -> BronzeRecord:
    return BronzeRecord(
        source_hash=source_hash,
        source_type=SourceType.SUPPLY_BEACH,
        row_index=1,
        raw={"Email": email, "Location": "Pune", "Chennai-open": "No", "Key Skills": "Java"},
    )


def _rolling_off(email: str, source_hash: str) -> BronzeRecord:
    return BronzeRecord(
        source_hash=source_hash,
        source_type=SourceType.SUPPLY_ROLLING_OFF,
        row_index=1,
        raw={"Email": email, "Roll-off Date": "2026-06-20", "Confidence": "medium"},
    )


def test_valid_as_of_threaded_from_snapshot_map() -> None:
    records = [_beach("a@x.com", "sha256:beach")]
    out = normalize_run(
        records,
        snapshot_dates={"sha256:beach": date(2026, 6, 1)},
        taxonomy=_TAX,
        run_id="run-1",
    )
    assert out[0].valid_as_of == date(2026, 6, 1)
    assert out[0].extractor_version == "silver-v1"


def test_same_email_two_sheets_yields_two_records_one_id_no_merge() -> None:
    """NF-4: same email on beach + rolling_off → two records, one candidate_id, not merged."""
    records = [
        _beach("priya@acme.example", "sha256:beach"),
        _rolling_off("priya@acme.example", "sha256:ro"),
    ]
    out = normalize_run(
        records,
        snapshot_dates={"sha256:beach": date(2026, 6, 1), "sha256:ro": date(2026, 6, 1)},
        taxonomy=_TAX,
        run_id="run-1",
    )
    assert len(out) == 2
    assert {r.candidate_id for r in out} == {out[0].candidate_id}  # one shared id
    assert {r.source_type for r in out} == {
        SourceType.SUPPLY_BEACH,
        SourceType.SUPPLY_ROLLING_OFF,
    }


def test_normalize_run_is_deterministic() -> None:
    """NF-2: same input → identical output (byte-for-byte JSON)."""
    records = [
        _rolling_off("b@x.com", "sha256:ro"),
        _beach("a@x.com", "sha256:beach"),
    ]
    snaps: dict[str, date | None] = {
        "sha256:beach": date(2026, 6, 1),
        "sha256:ro": date(2026, 6, 1),
    }
    first = normalize_run(records, snapshot_dates=snaps, taxonomy=_TAX, run_id="run-1")
    second = normalize_run(records, snapshot_dates=snaps, taxonomy=_TAX, run_id="run-1")
    assert [r.model_dump_json() for r in first] == [r.model_dump_json() for r in second]


def test_skipped_records_drop_out() -> None:
    records = [
        _beach("a@x.com", "sha256:beach"),
        _beach("", "sha256:beach"),  # missing email → skipped (ID-4)
    ]
    out = normalize_run(
        records, snapshot_dates={"sha256:beach": None}, taxonomy=_TAX, run_id="run-1"
    )
    assert len(out) == 1
