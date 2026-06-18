"""Tests for the content-addressed blob store + records persistence (a-001 T-003, L-LAYOUT-2)."""

from pathlib import Path

from dsm.ingest.blobstore import LocalFSBlobStore, hash_bytes, read_records, write_records
from dsm.ingest.models import BronzeRecord, SourceType


def test_put_returns_prefixed_sha256_and_round_trips(tmp_path: Path) -> None:
    store = LocalFSBlobStore(tmp_path)
    data = b"verbatim bronze bytes"
    blob_hash = store.put(data)
    assert blob_hash.startswith("sha256:")
    assert len(blob_hash.removeprefix("sha256:")) == 64
    assert store.get(blob_hash) == data  # byte-identical round-trip


def test_hash_is_deterministic_and_matches_put(tmp_path: Path) -> None:
    store = LocalFSBlobStore(tmp_path)
    data = b"same bytes"
    assert store.put(data) == hash_bytes(data) == hash_bytes(data)


def test_put_twice_is_idempotent(tmp_path: Path) -> None:
    store = LocalFSBlobStore(tmp_path)
    data = b"dup content"
    h1 = store.put(data)
    h2 = store.put(data)
    assert h1 == h2
    blob_dir = tmp_path / "blobs" / "sha256"
    # exactly one stored blob, no leftover temp files
    stored = [p for p in blob_dir.iterdir() if not p.name.startswith(".tmp-")]
    assert len(stored) == 1
    assert not any(p.name.startswith(".tmp-") for p in blob_dir.iterdir())


def test_exists_true_only_when_stored(tmp_path: Path) -> None:
    store = LocalFSBlobStore(tmp_path)
    h = store.put(b"present")
    assert store.exists(h) is True
    assert store.exists("sha256:" + "0" * 64) is False


def test_put_empty_bytes(tmp_path: Path) -> None:
    store = LocalFSBlobStore(tmp_path)
    h = store.put(b"")
    assert store.exists(h) is True
    assert store.get(h) == b""


# ---------------------------------------------------------------------------
# Records persistence (L-LAYOUT-2)
# ---------------------------------------------------------------------------


def _sample_records(source_hash: str) -> list[BronzeRecord]:
    return [
        BronzeRecord(
            source_hash=source_hash,
            source_type=SourceType.SUPPLY_BEACH,
            row_index=0,
            raw={"Name": "Karan", "City": "Bengaluru"},
        ),
        BronzeRecord(
            source_hash=source_hash,
            source_type=SourceType.SUPPLY_BEACH,
            row_index=1,
            raw={"Name": "Priya", "City": "Bengaluru"},
        ),
    ]


def test_write_records_round_trip(tmp_path: Path) -> None:
    source_hash = "sha256:" + "a" * 64
    records = _sample_records(source_hash)
    dest = write_records(records, source_hash, tmp_path)
    assert dest.is_file()
    assert dest == tmp_path / "records" / f"{'a' * 64}.jsonl"
    loaded = read_records(source_hash, tmp_path)
    assert loaded == records


def test_write_records_atomic_overwrite(tmp_path: Path) -> None:
    source_hash = "sha256:" + "b" * 64
    records_v1 = _sample_records(source_hash)[:1]
    records_v2 = _sample_records(source_hash)
    write_records(records_v1, source_hash, tmp_path)
    write_records(records_v2, source_hash, tmp_path)
    loaded = read_records(source_hash, tmp_path)
    assert loaded == records_v2


def test_read_records_missing_returns_empty(tmp_path: Path) -> None:
    loaded = read_records("sha256:" + "c" * 64, tmp_path)
    assert loaded == []


def test_write_records_no_temp_leftovers(tmp_path: Path) -> None:
    source_hash = "sha256:" + "d" * 64
    write_records(_sample_records(source_hash), source_hash, tmp_path)
    records_dir = tmp_path / "records"
    assert not any(p.suffix == ".tmp" for p in records_dir.iterdir())
