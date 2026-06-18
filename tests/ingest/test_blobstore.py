"""Tests for the content-addressed blob store (a-001 T-003)."""

from pathlib import Path

from dsm.ingest.blobstore import LocalFSBlobStore, hash_bytes


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
