"""Bronze-layer ingestion contracts (Phase 1/2 of ee-ingestion-architecture §6).

Module-local models for the deterministic landing + parsing foundation. No normalization,
no identity resolution, no LLM — those live in later slices (silver/gold).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

# Frozen shared contracts (AD-060) — imported, never redefined. Silver *produces* these.
from dsm.models import AvailabilityState, Location, ProficiencyLevel

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


# ---------------------------------------------------------------------------
# Phase 3 — Silver → NormalizedRecord (ee-ingestion-architecture §6)
# ---------------------------------------------------------------------------
#
# These are ingest-local, pre-canonical types: silver skills may be unmapped, unverified,
# or proficiency-less, and a record's location/grade may be absent. The frozen serving
# contract (dsm/models.py) is reused where it fits (Location, AvailabilityState,
# ProficiencyLevel) and never redefined here (structure.md: no duplicate model definitions).


class Grade(StrEnum):
    """Consultant grade parsed from the supply ``Grade`` column."""

    SENIOR_CONSULTANT = "senior_consultant"
    LEAD_CONSULTANT = "lead_consultant"
    PRINCIPAL_CONSULTANT = "principal_consultant"


class Confidence(StrEnum):
    """Roll-off confidence. Values match the frozen ``RollingOff.confidence`` Literal, so
    ``Confidence(...).value`` feeds the frozen model without touching the contract (AD-060)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class NormalizedSkill(BaseModel, frozen=True):
    """A silver-stage skill: canonical-or-verbatim name with provenance flags.

    Distinct from the frozen serving ``Skill`` (which requires proficiency and has no
    provenance) — silver skills are pre-canonical. This is the "unmapped/unverified-skill
    handling" the spec keeps ingest-local.
    """

    name: str  # canonical taxonomy id, or the verbatim surface form when unmapped
    proficiency: ProficiencyLevel | None = None  # absent for supply / CV-derived skills
    unmapped: bool = False  # raw skill not found in the taxonomy → queued (TX-2)
    unverified: bool = False  # AD-032: new-joiner CV-derived skill, counted not penalised


class NormalizedRecord(BaseModel, frozen=True):
    """Typed, normalized, identity-resolved silver record — one per source row/item (§6).

    Per-source (carries ``source_type``/``source_hash``); merge across records sharing a
    ``candidate_id`` is the gold stage, not silver.
    """

    candidate_id: str  # HMAC(email); the raw email is never stored here (ID-5)
    source_type: SourceType
    source_hash: str
    valid_as_of: date | None = None  # from the snapshot banner (VAOF-1)
    grade: Grade | None = None
    location: Location | None = None  # frozen Location; city is optional (AD-075)
    availability: AvailabilityState | None = None  # frozen union; None for resume/feedback
    skills: list[NormalizedSkill] = Field(default_factory=list)
    raw_text: str | None = None  # resume body / feedback item → later enrichment
    parse_warnings: list[str] = Field(default_factory=list)  # lossy mappings (e.g. LOC-2)
    extractor_version: str
