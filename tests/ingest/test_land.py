"""Tests for Step 1 landing (a-001 T-005)."""

from pathlib import Path

from dsm.ingest.blobstore import LocalFSBlobStore, hash_bytes
from dsm.ingest.land import classify, land
from dsm.ingest.manifest import JSONLManifest
from dsm.ingest.models import LandingStatus, SourceType


def _raw_dir(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    for sub in ("supply", "resumes", "feedback"):
        (raw / sub).mkdir(parents=True)
    return raw


def _stores(tmp_path: Path) -> tuple[LocalFSBlobStore, JSONLManifest]:
    return (
        LocalFSBlobStore(tmp_path / "bronze"),
        JSONLManifest(tmp_path / "bronze" / "manifest.jsonl"),
    )


def _blob_count(tmp_path: Path) -> int:
    blob_dir = tmp_path / "bronze" / "blobs" / "sha256"
    return sum(1 for p in blob_dir.iterdir() if not p.name.startswith(".tmp-"))


def test_classify_table(tmp_path: Path) -> None:
    raw = _raw_dir(tmp_path)
    assert classify(raw / "supply" / "beach.csv") is SourceType.SUPPLY_BEACH
    assert classify(raw / "supply" / "rolling_off.csv") is SourceType.SUPPLY_ROLLING_OFF
    assert classify(raw / "supply" / "new_joiners.csv") is SourceType.SUPPLY_NEW_JOINERS
    assert classify(raw / "resumes" / "anyone.pdf") is SourceType.RESUME
    assert classify(raw / "feedback" / "anyone.md") is SourceType.FEEDBACK
    # unrecognized
    assert classify(raw / "supply" / "notes.txt") is None
    assert classify(raw / "resumes" / "cv.docx") is None
    assert classify(raw / "feedback" / "note.txt") is None


def test_classify_real_filenames_case_and_separator_insensitive(tmp_path: Path) -> None:
    """Real supply files use Title Case + spaces (e.g. 'Rolling Off.csv')."""
    raw = _raw_dir(tmp_path)
    assert classify(raw / "supply" / "Beach.csv") is SourceType.SUPPLY_BEACH
    assert classify(raw / "supply" / "Rolling Off.csv") is SourceType.SUPPLY_ROLLING_OFF
    assert classify(raw / "supply" / "New Joiners.csv") is SourceType.SUPPLY_NEW_JOINERS


def test_new_file_is_landed_and_blob_written(tmp_path: Path) -> None:
    raw = _raw_dir(tmp_path)
    (raw / "supply" / "beach.csv").write_bytes(b"as of 2026-06-01\nName\nAarav\n")
    blobs, manifest = _stores(tmp_path)

    entries = land(raw, blobs, manifest, run_id="run-1")

    assert len(entries) == 1
    assert entries[0].status is LandingStatus.LANDED
    assert blobs.exists(entries[0].raw_bytes_hash)  # type: ignore[arg-type]
    assert manifest.has_hash(entries[0].raw_bytes_hash)  # type: ignore[arg-type]


def test_unclassifiable_file_is_invalid_no_blob(tmp_path: Path) -> None:
    raw = _raw_dir(tmp_path)
    (raw / "supply" / "notes.txt").write_bytes(b"junk")
    blobs, manifest = _stores(tmp_path)

    entries = land(raw, blobs, manifest, run_id="run-1")

    assert len(entries) == 1
    e = entries[0]
    assert e.status is LandingStatus.INVALID
    assert e.source_type is None
    assert e.raw_bytes_hash is None
    assert _blob_count(tmp_path) == 0
    # INVALID is not committed to the manifest as a dedup authority
    assert manifest.entries_for_run("run-1") == []


def test_idempotent_reland_skips_all(tmp_path: Path) -> None:
    raw = _raw_dir(tmp_path)
    (raw / "supply" / "beach.csv").write_bytes(b"as of 2026-06-01\nName\nAarav\n")
    (raw / "feedback" / "fb.md").write_bytes(b"email: a@x.com\ngreat work")
    blobs, manifest = _stores(tmp_path)

    first = land(raw, blobs, manifest, run_id="run-1")
    assert {e.status for e in first} == {LandingStatus.LANDED}
    blobs_after_first = _blob_count(tmp_path)

    second = land(raw, blobs, manifest, run_id="run-2")
    assert {e.status for e in second} == {LandingStatus.SKIPPED}
    assert _blob_count(tmp_path) == blobs_after_first  # zero new blobs


def test_commit_marker_recovery(tmp_path: Path) -> None:
    """Crash after blob write, before manifest append: re-land yields one blob + one entry."""
    raw = _raw_dir(tmp_path)
    data = b"as of 2026-06-01\nName\nAarav\n"
    (raw / "supply" / "beach.csv").write_bytes(data)
    blobs, manifest = _stores(tmp_path)

    # Simulate the crash: blob exists, but no manifest entry was committed.
    blobs.put(data)
    assert blobs.exists(hash_bytes(data))
    assert not manifest.has_hash(hash_bytes(data))

    entries = land(raw, blobs, manifest, run_id="recover")

    assert len(entries) == 1
    assert entries[0].status is LandingStatus.LANDED
    assert _blob_count(tmp_path) == 1  # no duplicate blob
    landed = [e for e in manifest.entries_for_run("recover") if e.status is LandingStatus.LANDED]
    assert len(landed) == 1


def test_duplicate_content_under_different_names_is_skipped(tmp_path: Path) -> None:
    raw = _raw_dir(tmp_path)
    content = b"email: a@x.com\nsame feedback"
    (raw / "feedback" / "fb1.md").write_bytes(content)
    (raw / "feedback" / "fb2.md").write_bytes(content)
    blobs, manifest = _stores(tmp_path)

    entries = land(raw, blobs, manifest, run_id="run-1")

    statuses = [e.status for e in entries]
    assert statuses.count(LandingStatus.LANDED) == 1
    assert statuses.count(LandingStatus.SKIPPED) == 1
    assert _blob_count(tmp_path) == 1
