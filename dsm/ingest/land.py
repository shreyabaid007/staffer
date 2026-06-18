"""Step 1 — Land: discover, classify, hash, dedup, write bronze + manifest.

Deterministic and LLM-free. Same bytes → same hash → idempotent re-land. The blob is written
*before* the manifest entry is appended: the manifest append is the commit marker, so a crash
between the two simply re-lands cleanly on the next run (ee-ingestion-architecture §10).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from pathlib import Path

from dsm.ingest.blobstore import BlobStore, hash_bytes
from dsm.ingest.manifest import Manifest
from dsm.ingest.models import LandingStatus, ManifestEntry, SourceType
from dsm.ingest.parse.csv import read_banner_date

_log = logging.getLogger(__name__)  # T-009 swaps invalid logging to lineage.log_invalid

_SUPPLY_FILES = {
    "beach.csv": SourceType.SUPPLY_BEACH,
    "rolling_off.csv": SourceType.SUPPLY_ROLLING_OFF,
    "new_joiners.csv": SourceType.SUPPLY_NEW_JOINERS,
}
_SUPPLY_TYPES = frozenset(_SUPPLY_FILES.values())


def classify(path: Path) -> SourceType | None:
    """Map a raw file to its ``SourceType`` by directory + name, or ``None`` if unrecognized.

    Pure function (no I/O) so it is unit-testable in isolation (LAND-CLASSIFY-1).
    """
    category = path.parent.name
    if category == "supply":
        return _SUPPLY_FILES.get(path.name)
    if category == "resumes":
        return SourceType.RESUME if path.suffix.lower() == ".pdf" else None
    if category == "feedback":
        return SourceType.FEEDBACK if path.suffix.lower() == ".md" else None
    return None


def _discover(raw_root: Path) -> list[Path]:
    """Every raw file under ``raw_root`` in deterministic sorted order (LAND-DISCOVER-1)."""
    return sorted(p for p in raw_root.rglob("*") if p.is_file() and p.name != ".gitkeep")


def _snapshot_date(source_type: SourceType, data: bytes) -> date | None:
    """Snapshot date stamped on the manifest entry. Supply CSVs carry an ``as of`` banner;
    resumes/feedback have none (LAND-ASOF-1/2)."""
    if source_type in _SUPPLY_TYPES:
        return read_banner_date(data)
    return None


def land(
    raw_root: Path,
    blobs: BlobStore,
    manifest: Manifest,
    run_id: str,
) -> list[ManifestEntry]:
    """Land every file under ``raw_root`` into the bronze layer, returning one entry per file."""
    entries: list[ManifestEntry] = []
    for path in _discover(raw_root):
        source_uri = str(path)
        discovered_at = datetime.now(UTC)
        source_type = classify(path)

        if source_type is None:
            _log.warning(
                "invalid: unclassifiable file",
                extra={"reason": "unclassifiable", "payload": source_uri, "run_id": run_id},
            )
            entries.append(
                ManifestEntry(
                    run_id=run_id,
                    source_uri=source_uri,
                    source_type=None,
                    raw_bytes_hash=None,
                    size_bytes=path.stat().st_size,
                    discovered_at=discovered_at,
                    status=LandingStatus.INVALID,
                )
            )
            continue

        data = path.read_bytes()
        raw_bytes_hash = hash_bytes(data)

        if manifest.has_hash(raw_bytes_hash):
            status = LandingStatus.SKIPPED  # idempotent re-land, no blob write
        else:
            blobs.put(data)  # blob FIRST, then manifest append (commit marker)
            status = LandingStatus.LANDED

        entry = ManifestEntry(
            run_id=run_id,
            source_uri=source_uri,
            source_type=source_type,
            raw_bytes_hash=raw_bytes_hash,
            size_bytes=len(data),
            discovered_at=discovered_at,
            snapshot_date=_snapshot_date(source_type, data),
            status=status,
        )
        manifest.append(entry)
        entries.append(entry)
    return entries
