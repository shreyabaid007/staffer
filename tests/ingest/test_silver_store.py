"""Tests for silver-layer persistence (a-002 T-008-WRITE; SW-1)."""

from datetime import date
from pathlib import Path

from dsm.ingest.models import NormalizedRecord, SourceType
from dsm.ingest.silver import read_normalized, write_normalized
from dsm.models import FreeNow


def _record() -> NormalizedRecord:
    return NormalizedRecord(
        candidate_id="cid:abc",
        source_type=SourceType.SUPPLY_BEACH,
        source_hash="sha256:beach",
        valid_as_of=date(2026, 6, 1),
        availability=FreeNow(),
        extractor_version="silver-v1",
    )


def test_write_then_read_round_trips(tmp_path: Path) -> None:
    records = [_record()]
    dest = write_normalized(records, "sha256:beach", tmp_path)
    assert dest.name == "beach.jsonl"
    assert dest.parent == tmp_path / "records"
    back = read_normalized("sha256:beach", tmp_path)
    assert back == records


def test_write_is_idempotent(tmp_path: Path) -> None:
    write_normalized([_record()], "sha256:beach", tmp_path)
    write_normalized([_record()], "sha256:beach", tmp_path)
    assert len(read_normalized("sha256:beach", tmp_path)) == 1


def test_read_missing_returns_empty(tmp_path: Path) -> None:
    assert read_normalized("sha256:nope", tmp_path) == []


def test_jsonl_has_one_line_per_record(tmp_path: Path) -> None:
    write_normalized([_record(), _record()], "sha256:beach", tmp_path)
    text = (tmp_path / "records" / "beach.jsonl").read_text(encoding="utf-8")
    assert text.count("\n") == 2
