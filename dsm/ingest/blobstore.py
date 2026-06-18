"""Content-addressed blob store for verbatim bronze bytes (ee-ingestion-architecture §4).

The MVP backend is the local filesystem; the ``BlobStore`` protocol keeps the backend
swappable (object storage with encryption-at-rest + IAM later, per AD-066).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Protocol

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
