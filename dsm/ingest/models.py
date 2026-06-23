"""Bronze-layer ingestion contracts (Phase 1/2 of ee-ingestion-architecture §6).

Module-local models for the deterministic landing + parsing foundation. No normalization,
no identity resolution, no LLM — those live in later slices (silver/gold).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

# Frozen shared contracts (AD-060) — imported, never redefined. Silver *produces* these.
from dsm.models import AvailabilityState, EvidenceCitation, Location, ProficiencyLevel

# ``Grade`` moved to the shared home in AD-091; re-exported here (redundant alias = explicit
# re-export) so existing ``from dsm.ingest.models import Grade`` call sites keep working.
from dsm.models import Grade as Grade

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


# ---------------------------------------------------------------------------
# Phase 4 — Enrich (LLM extraction outputs; ee-ingestion-architecture §6)
# ---------------------------------------------------------------------------
#
# Typed return types for the DSPy signatures in ``dsm/ingest/enrich.py``. Every extracted fact
# carries an ``EvidenceCitation`` (the frozen, AD-077-relaxed one) whose ``text`` is verified
# present in the source before the fact is accepted (AD-073). These are ingest-local: the LLM
# emits surface forms, pre-normalization and pre-merge.


class SkillExtraction(BaseModel, frozen=True):
    """One skill the LLM pulled from a resume, with its evidence (resume LLM output)."""

    name: str  # surface form, pre-taxonomy-normalization
    proficiency: ProficiencyLevel | None = None
    evidence: EvidenceCitation


class ProfileSummaryExtraction(BaseModel, frozen=True):
    """Structured facts extracted from one anonymized resume (resume LLM output, §6 Phase 4)."""

    skills: list[SkillExtraction] = Field(default_factory=list)
    employers: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    seniority_signals: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)


class FeedbackExtraction(BaseModel, frozen=True):
    """Signals from **one** feedback item (renamed from §6 ``FeedbackSignals`` to avoid colliding
    with the frozen aggregate ``dsm.models.FeedbackSignals``). Aggregated at gold; the
    feedback *score* is computed downstream at match time, never here (FB-2/AD-079)."""

    confirmed_skills: list[str] = Field(default_factory=list)
    skill_gaps: list[str] = Field(
        default_factory=list
    )  # skills the feedback denies / flags as weak
    domain_confirmation: str | None = None
    sentiment: Literal["very_positive", "positive", "neutral", "negative"]
    retention_requested: bool = False  # client "keep them" — a match-time +modifier (AD-023)
    rejection_requested: bool = False  # client "do not staff" — a match-time −modifier
    summary: str
    evidence: EvidenceCitation


# ---------------------------------------------------------------------------
# Phase 5 — Gold (canonical, sourced entity; ee-ingestion-architecture §6/§7)
# ---------------------------------------------------------------------------


class Sourced[T](BaseModel, frozen=True):
    """A merged value with its supporting citations and a confidence band (§6/§7)."""

    value: T
    citations: list[EvidenceCitation] = Field(default_factory=list)
    confidence: Confidence = Confidence.MEDIUM


class MergedSkill(BaseModel, frozen=True):
    """A skill on the canonical entity after provenance-weighted merge (§7).

    ``demonstrated`` is feedback-verified truth (feedback > resume): ``True`` confirmed, ``False``
    denied, ``None`` unverified. ``conflict`` is set when resume and feedback disagree — both
    citations are kept and the disagreement is recorded, never averaged (MG-5).
    """

    name: str  # canonical taxonomy id
    proficiency: ProficiencyLevel | None = None  # resume > CSV (CSV carries none)
    demonstrated: bool | None = None  # feedback > resume; None = unverified
    unverified: bool = False  # AD-032 new-joiner provenance, carried from silver
    confidence: Confidence = Confidence.MEDIUM
    citations: list[EvidenceCitation] = Field(default_factory=list)
    conflict: str | None = None  # resume↔feedback disagreement (MG-5)


class GoldCandidate(BaseModel, frozen=True):
    """One canonical, cited, conflict-aware entity per consultant (§6 Phase 5, renamed from
    ``Candidate`` to avoid colliding with the frozen serving ``dsm.models.Candidate``).

    Identity is carried as vault references only — never the raw name/email (GS-4/AD-068). Supply
    fields are optional so thin/medium/rich profiles all yield a valid entity (PP-1..3). Carries
    the cited feedback *facts*; the feedback *score* is a match-time concern (FB-2/AD-079).
    """

    candidate_id: str
    name_vault_ref: str
    email_vault_ref: str
    grade: Sourced[Grade] | None = None
    location: Sourced[Location] | None = None
    availability: Sourced[AvailabilityState] | None = None
    skills: list[MergedSkill] = Field(default_factory=list)
    domains: list[Sourced[str]] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    feedback: list[FeedbackExtraction] = Field(default_factory=list)
    valid_as_of: date | None = None
    is_tombstoned: bool = False  # set by snapshot reconciliation (RC-1)
    conflicts: list[str] = Field(
        default_factory=list
    )  # candidate-level roll-up of MergedSkill.conflict
    gold_hash: str  # change-detection for re-index (GS-2)
    merge_version: str
    prompt_version: str
    model_version: str
