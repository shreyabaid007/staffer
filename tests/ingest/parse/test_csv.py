"""Tests for CSV bronze parsing against golden fixtures (a-001 T-006)."""

from datetime import date
from pathlib import Path

from dsm.ingest.models import BronzeRecord, SourceType
from dsm.ingest.parse.csv import parse_csv, read_banner_date

_FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "ingest"


def _read(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


def test_read_banner_date_parses_iso() -> None:
    assert read_banner_date(_read("beach.csv")) == date(2026, 6, 1)


def test_read_banner_date_none_when_absent() -> None:
    assert read_banner_date(_read("no_banner.csv")) is None


def test_parse_csv_golden_records() -> None:
    records = parse_csv(_read("beach.csv"), SourceType.SUPPLY_BEACH, "sha256:beach", run_id="t")
    assert records == [
        BronzeRecord(
            source_hash="sha256:beach",
            source_type=SourceType.SUPPLY_BEACH,
            row_index=0,
            raw={"Name": "Aarav", "Days on Beach": "12", "City": "Chennai"},
        ),
        BronzeRecord(
            source_hash="sha256:beach",
            source_type=SourceType.SUPPLY_BEACH,
            row_index=1,
            raw={"Name": "Vikram", "Days on Beach": "3", "City": "Pune"},
        ),
    ]


def test_parse_csv_honors_quoting() -> None:
    records = parse_csv(_read("quoted.csv"), SourceType.SUPPLY_BEACH, "sha256:q", run_id="t")
    assert records[0].raw["Note"] == "Hello, world"  # quoted comma stays one cell
    assert records[1].raw["Note"] == "line1\nline2"  # quoted newline stays one cell


def test_parse_csv_logs_and_skips_malformed_row() -> None:
    records = parse_csv(_read("malformed.csv"), SourceType.SUPPLY_BEACH, "sha256:m", run_id="t")
    # BadRow (2 cols vs 3) is skipped; valid rows keep their source-position row_index.
    assert [r.raw["Name"] for r in records] == ["Aarav", "Vikram"]
    assert [r.row_index for r in records] == [0, 2]


def test_parse_csv_no_banner_still_parses() -> None:
    records = parse_csv(_read("no_banner.csv"), SourceType.SUPPLY_BEACH, "sha256:nb", run_id="t")
    assert len(records) == 1
    assert records[0].raw == {"Name": "Aarav", "City": "Chennai"}


def test_parse_csv_is_deterministic() -> None:
    data = _read("beach.csv")
    first = parse_csv(data, SourceType.SUPPLY_BEACH, "sha256:beach", run_id="t")
    second = parse_csv(data, SourceType.SUPPLY_BEACH, "sha256:beach", run_id="t")
    assert first == second


def test_empty_csv_returns_no_records() -> None:
    assert parse_csv(b"", SourceType.SUPPLY_BEACH, "sha256:e", run_id="t") == []
