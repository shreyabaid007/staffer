"""Bronze-layer ingestion contracts (Phase 1/2 of ee-ingestion-architecture §6).

Module-local models for the deterministic landing + parsing foundation. No normalization,
no identity resolution, no LLM — those live in later slices (silver/gold).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SourceType(StrEnum):
    """Origin of a landed file. Finer-grained than CandidateSource: each supply sheet
    is its own type, and resumes/feedback are distinct unstructured sources."""

    SUPPLY_BEACH = "supply_beach"
    SUPPLY_ROLLING_OFF = "supply_rolling_off"
    SUPPLY_NEW_JOINERS = "supply_new_joiners"
    RESUME = "resume"
    FEEDBACK = "feedback"


class LandingStatus(StrEnum):
    """Outcome of landing a single file."""

    LANDED = "landed"  # new bytes, stored
    SKIPPED = "skipped"  # content hash already seen (idempotent no-op)
    INVALID = "invalid"  # could not classify or read


# ---------------------------------------------------------------------------
# Phase 1 — Land → ManifestEntry
# ---------------------------------------------------------------------------


class ManifestEntry(BaseModel, frozen=True):
    """One line in the append-only landing log (also the crash-recovery commit marker).

    ``source_type``/``raw_bytes_hash`` are ``None`` for INVALID entries — files that could
    not be classified or read have neither (a-001 design.md, §6 divergence resolved).
    """

    run_id: str
    source_uri: str
    source_type: SourceType | None  # None when unclassifiable (status=INVALID)
    raw_bytes_hash: str | None  # "sha256:..."; None when not read
    size_bytes: int
    discovered_at: datetime
    snapshot_date: date | None = None  # parsed from CSV banner; None for pdf/md/invalid
    status: LandingStatus


# ---------------------------------------------------------------------------
# Phase 2 — Parse → BronzeRecord
# ---------------------------------------------------------------------------


class BronzeRecord(BaseModel, frozen=True):
    """A single parsed row/item, verbatim. Shape of ``raw`` depends on ``source_type``:

    - CSV: original column names → string values, verbatim.
    - Resume: ``{"text": str, "sections": list[str], "email_found": str}``.
    - Feedback: ``{"email_key": str, "raw_markdown": str, "kind": "project"|"client"}``.
    """

    source_hash: str
    source_type: SourceType
    row_index: int
    raw: dict[str, str | list[str]]


# ---------------------------------------------------------------------------
# Observability — run manifest (lineage seed, §12)
# ---------------------------------------------------------------------------


class RunManifest(BaseModel, frozen=True):
    """Per-run summary: every file seen plus landed/skipped/invalid tallies."""

    run_id: str
    entries: list[ManifestEntry]
    landed: int
    skipped: int
    invalid: int
