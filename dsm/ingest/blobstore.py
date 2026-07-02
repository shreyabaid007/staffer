"""Content-addressed bronze storage (ee-ingestion-architecture §4).

Two concerns live here because they share the content-addressed pattern and the
bronze directory tree:

1. **BlobStore** — verbatim source bytes at ``blobs/sha256/<hex>``.
2. **Records persistence** — parsed ``BronzeRecord`` JSONL at ``records/<hex>.jsonl``
   (L-LAYOUT-2). Standalone functions, not part of the ``BlobStore`` protocol.

The MVP backend is the local filesystem; the ``BlobStore`` protocol keeps the backend
swappable (object storage with encryption-at-rest + IAM later, per AD-066).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from dsm.ingest.models import BronzeRecord

_HASH_PREFIX = "sha256:"


def hash_bytes(data: bytes) -> str:
    """Return the content hash of ``data`` as ``"sha256:<hex>"``.

    The single definition of how bytes become a hash — shared by the blob store and the
    lander so the same bytes always yield the same address (LAND-HASH-1, B-PUT-1).
    """
    return f"{_HASH_PREFIX}{hashlib.sha256(data).hexdigest()}"


class BlobStore(Protocol):
    """Stores immutable bytes addressed by their content hash."""

    def put(self, data: bytes) -> str:
        """Store ``data`` and return its ``"sha256:<hex>"`` hash."""
        ...

    def get(self, blob_hash: str) -> bytes:
        """Return the exact bytes previously stored under ``blob_hash``."""
        ...

    def exists(self, blob_hash: str) -> bool:
        """Return whether a blob is stored for ``blob_hash``."""
        ...


class LocalFSBlobStore:
    """Filesystem-backed blob store: ``<root>/blobs/sha256/<hex>``.

    Writes are atomic (temp file + ``os.replace``) so a crash mid-write never leaves a
    partial blob at the final content-addressed path (B-PUT-3).
    """

    def __init__(self, root: Path) -> None:
        self._dir = Path(root) / "blobs" / "sha256"
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, blob_hash: str) -> Path:
        hex_digest = blob_hash.removeprefix(_HASH_PREFIX)
        return self._dir / hex_digest

    def put(self, data: bytes) -> str:
        blob_hash = hash_bytes(data)
        final = self._path(blob_hash)
        # Content-addressed: identical bytes target the same path. The temp+rename is safe
        # to repeat over an existing blob (idempotent, B-PUT-2).
        tmp = self._dir / f".tmp-{blob_hash.removeprefix(_HASH_PREFIX)}"
        tmp.write_bytes(data)
        os.replace(tmp, final)
        return blob_hash

    def get(self, blob_hash: str) -> bytes:
        return self._path(blob_hash).read_bytes()

    def exists(self, blob_hash: str) -> bool:
        return self._path(blob_hash).is_file()


# ---------------------------------------------------------------------------
# Bronze records persistence (L-LAYOUT-2)
# ---------------------------------------------------------------------------


def _records_dir(bronze_root: Path) -> Path:
    return bronze_root / "records"


def _records_path(bronze_root: Path, source_hash: str) -> Path:
    hex_digest = source_hash.removeprefix(_HASH_PREFIX)
    return _records_dir(bronze_root) / f"{hex_digest}.jsonl"


def write_records(
    records: list[BronzeRecord],
    source_hash: str,
    bronze_root: Path,
) -> Path:
    """Persist parsed ``BronzeRecord``s to ``records/<hex>.jsonl`` (L-LAYOUT-2).

    Atomic temp+rename, consistent with blob writes. Idempotent: re-writing the
    same records for the same source hash overwrites with identical content.

    Args:
        records: the parsed records for one source file.
        source_hash: the ``"sha256:<hex>"`` hash of the source blob.
        bronze_root: the bronze layer root (e.g. ``data/bronze``).

    Returns:
        The path written to.
    """
    dest = _records_path(bronze_root, source_hash)
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(r.model_dump_json() + "\n" for r in records)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, dest)
    return dest


def has_records(source_hash: str, bronze_root: Path) -> bool:
    """Whether a persisted records file exists for ``source_hash``.

    Distinguishes "never persisted" from a legitimately **empty** records file (an invalid
    source that parsed to zero records) — ``read_records`` returns ``[]`` for both, but only
    the former should trigger a re-parse fallback (c-011 full-corpus read-back).
    """
    return _records_path(bronze_root, source_hash).is_file()


def read_records(source_hash: str, bronze_root: Path) -> list[BronzeRecord]:
    """Read back ``BronzeRecord``s for a source hash from ``records/<hex>.jsonl``.

    Args:
        source_hash: the ``"sha256:<hex>"`` hash of the source blob.
        bronze_root: the bronze layer root (e.g. ``data/bronze``).

    Returns:
        The list of records in file order, or an empty list if no file exists.
    """
    from dsm.ingest.models import BronzeRecord as _BR

    path = _records_path(bronze_root, source_hash)
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as fh:
        return [_BR.model_validate_json(line) for line in fh if line.strip()]
