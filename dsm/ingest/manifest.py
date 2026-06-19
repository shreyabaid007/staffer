"""Append-only landing manifest (ee-ingestion-architecture §4, §10).

The manifest is the catalog of landed files and the crash-recovery commit marker: a content
hash counts as landed only once its ``ManifestEntry`` line is appended. The ``Manifest``
protocol keeps the backend swappable (JSONL now, SQLite when the catalog needs queries).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from dsm.ingest.models import LandingStatus, ManifestEntry


class Manifest(Protocol):
    """Records landing outcomes and answers dedup/lineage queries."""

    def append(self, entry: ManifestEntry) -> None:
        """Append one landing entry (the commit marker — never rewrites prior lines)."""
        ...

    def has_hash(self, raw_bytes_hash: str) -> bool:
        """Return whether ``raw_bytes_hash`` was previously LANDED."""
        ...

    def entries_for_run(self, run_id: str) -> list[ManifestEntry]:
        """Return all entries for ``run_id`` in append order."""
        ...


class JSONLManifest:
    """JSON-lines manifest: one ``ManifestEntry`` per line in ``manifest.jsonl``."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _read_all(self) -> list[ManifestEntry]:
        if not self._path.is_file():
            return []
        with self._path.open(encoding="utf-8") as fh:
            return [ManifestEntry.model_validate_json(line) for line in fh if line.strip()]

    def append(self, entry: ManifestEntry) -> None:
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(entry.model_dump_json() + "\n")

    def has_hash(self, raw_bytes_hash: str) -> bool:
        # Only a prior LANDED entry is the dedup authority; SKIPPED/INVALID entries never
        # satisfy has_hash, so a previously-skipped or invalid file is reconsidered.
        return any(
            e.raw_bytes_hash == raw_bytes_hash and e.status is LandingStatus.LANDED
            for e in self._read_all()
        )

    def entries_for_run(self, run_id: str) -> list[ManifestEntry]:
        return [e for e in self._read_all() if e.run_id == run_id]
