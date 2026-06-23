"""Shared domain contracts for the Demand–Supply Matcher (frozen after Slice 0 — AD-060)."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ProficiencyLevel(StrEnum):
    """Skill proficiency band."""

    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


class FeedbackSource(StrEnum):
    """Origin of a feedback entry."""

    INTERNAL_EE = "internal_ee"
    CLIENT = "client"


class CandidateSource(StrEnum):
    """Supply-sheet tab the candidate comes from."""

    BEACH = "beach"
    ROLLING_OFF = "rolling_off"
    NEW_JOINER = "new_joiner"


class Grade(StrEnum):
    """Consultant grade parsed from the supply ``Grade`` column.

    Shared home (AD-091): moved here from ``dsm.ingest.models`` (which now re-imports it) so the
    index models can carry a ``Grade`` facet without importing ``dsm.ingest``. Query-time
    seniority is a soft signal sourced from the index record, never a gate (AD-090).
    """

    SENIOR_CONSULTANT = "senior_consultant"
    LEAD_CONSULTANT = "lead_consultant"
    PRINCIPAL_CONSULTANT = "principal_consultant"


class SkillDepth(StrEnum):
    """How strictly a skill requirement must be met (AD-033)."""

    HARD = "hard"  # exact match required; adjacency never clears
    DESIRED = "desired"  # soft requirement; adjacency gives partial credit


class ExclusionReason(StrEnum):
    """Why a candidate was excluded by the deterministic gate (AD-002)."""

    LOCATION_MISMATCH = "location_mismatch"
    AVAILABILITY_MISMATCH = "availability_mismatch"
    HARD_SKILL_MISMATCH = "hard_skill_mismatch"  # exact hard-skill filter (AD-088)


class FlagType(StrEnum):
    """Trade-off or warning surfaced in the candidate assessment."""

    UNVERIFIED_SKILLS = "unverified_skills"  # AD-032: new joiner
    ADJACENCY_USED = "adjacency_used"  # AD-033: partial credit
    ROLL_OFF_UNCERTAIN = "roll_off_uncertain"  # AD-022: low confidence
    RETENTION_RISK = "retention_risk"  # AD-023: client wants to keep
    FRESHNESS_WARNING = "freshness_warning"  # AD-092: supply snapshot stale-but-usable (warn)


class EvidenceSource(StrEnum):
    """Where an evidence citation came from."""

    SUPPLY_SHEET = "supply_sheet"
    PROFILE_PDF = "profile_pdf"
    FEEDBACK = "feedback"


# ---------------------------------------------------------------------------
# Input layer
# ---------------------------------------------------------------------------


class Location(BaseModel, frozen=True):
    """Geographic location and remote/onsite eligibility (AD-086).

    The overloaded ``remote_eligible`` boolean is split into two orthogonal facets:
    ``remote_within_country`` ("works remote from a home base") and ``onsite_cities``
    (cities beyond ``city`` where the candidate will work onsite). ``remote_within_country``
    never clears an onsite (co-location) gate — only a city match or onsite-city membership does.
    """

    city: str | None = None  # None for "Remote (India)" — no base city (AD-075)
    state: str | None = None
    country: str = "India"
    remote_within_country: bool = False  # "Remote (India)" in the data (AD-086)
    onsite_cities: frozenset[str] = frozenset()  # extra onsite cities, e.g. Chennai-open (AD-086)


class Skill(BaseModel, frozen=True):
    """A candidate skill with proficiency level."""

    name: str  # normalised lowercase (e.g., "kotlin", "react")
    proficiency: ProficiencyLevel


class FreeNow(BaseModel, frozen=True):
    """Availability variant: candidate is immediately available."""

    type: Literal["free_now"] = "free_now"


class RollingOff(BaseModel, frozen=True):
    """Availability variant: candidate is rolling off a project."""

    type: Literal["rolling_off"] = "rolling_off"
    expected_date: date
    confidence: Literal["high", "medium", "low"]  # AD-022: flag, not gate


class NewJoiner(BaseModel, frozen=True):
    """Availability variant: candidate is a new joiner."""

    type: Literal["new_joiner"] = "new_joiner"
    join_date: date


AvailabilityState = Annotated[
    FreeNow | RollingOff | NewJoiner,
    Field(discriminator="type"),
]


class FeedbackEntry(BaseModel, frozen=True):
    """A single piece of feedback from a named source."""

    source: FeedbackSource
    text: str
    sentiment: Literal["positive", "neutral", "negative"] | None = None
    retention_flag: bool = False  # AD-023: "keep them" → surfaces as trade-off


class FeedbackSignals(BaseModel, frozen=True):
    """Aggregated feedback for a candidate (AD-031: EE and client weighted equally)."""

    entries: list[FeedbackEntry] = Field(default_factory=list)


class Candidate(BaseModel, frozen=True):
    """A person from the supply sheets."""

    email: str  # join key (AD-012)
    name: str
    location: Location
    availability: AvailabilityState
    skills: list[Skill]
    feedback: FeedbackSignals
    source: CandidateSource
    # Enrichment fields (nullable until profiles ingested):
    profile_summary: str | None = None
    years_experience: int | None = None


class SkillRequirement(BaseModel, frozen=True):
    """A role's skill requirement with depth indicator."""

    name: str  # normalised lowercase
    depth: SkillDepth
    min_proficiency: ProficiencyLevel | None = None


class OpenRole(BaseModel, frozen=True):
    """The input role (before clarification)."""

    role_id: str
    title: str
    required_skills: list[SkillRequirement]  # raw from input; clarify may refine
    preferred_skills: list[str] = Field(default_factory=list)
    location: Location
    co_location_required: bool  # AD-020: the hard gate flag
    start_date: date
    description: str | None = None  # free text for clarification


# ---------------------------------------------------------------------------
# Phase outputs
# ---------------------------------------------------------------------------


class TargetProfileScorecard(BaseModel, frozen=True):
    """Output of match/clarify — the LLM's structured interpretation of the role."""

    role_id: str
    hard_depth_skills: list[SkillRequirement]  # depth=HARD; gate enforces exact match
    desired_skills: list[SkillRequirement]  # depth=DESIRED; adjacency allowed
    location: Location
    co_location_required: bool
    start_date: date
    availability_window_days: int = 14  # AD-021
    clarification_notes: str | None = None  # LLM's reasoning


class Exclusion(BaseModel, frozen=True):
    """A single gate exclusion record."""

    candidate_email: str
    reason: ExclusionReason
    detail: str  # human-readable specifics


class ExclusionLog(BaseModel, frozen=True):
    """All candidates excluded by the deterministic gate."""

    exclusions: list[Exclusion]


class EligiblePool(BaseModel, frozen=True):
    """Candidates that passed all gates."""

    candidates: list[Candidate]
    scorecard_id: str  # for traceability


class Flag(BaseModel, frozen=True):
    """A trade-off or warning to surface in the shortlist."""

    type: FlagType
    message: str


class EvidenceCitation(BaseModel, frozen=True):
    """Links a claim to its verbatim source (AD-040; AD-073).

    ``text`` is the AD-073 **verified verbatim quote** — a span confirmed to exist in the source
    before the claim is accepted. ``source_hash``/``locator`` add optional lineage-to-source
    (which bronze blob + where inside it) for the enrich/gold stage; both default to ``None`` so
    existing citations (gates/score/rank) are unaffected (AD-077, backwards-compatible).
    """

    source: EvidenceSource
    text: str  # the verbatim snippet — verified present in the source (AD-073)
    source_hash: str | None = None  # "sha256:..." — which bronze blob the quote came from (AD-077)
    locator: str | None = None  # where in it, e.g. "resume p1 SKILLS" | "feedback fb_0" (AD-077)
    metadata: dict[str, str] = Field(default_factory=dict)  # e.g., {"page": "2"}


class CandidateAssessment(BaseModel, frozen=True):
    """Scored candidate with LLM-generated reasoning."""

    candidate: Candidate
    skill_match_score: float  # 0.0–1.0
    feedback_score: float  # 0.0–1.0
    combined_score: float  # 0.7*skill + 0.3*feedback (AD-030)
    flags: list[Flag]
    evidence: list[EvidenceCitation]
    narrative: str  # 1–2 sentence explanation
    # Sub-scores for transparency:
    hard_skill_coverage: float  # fraction of hard skills matched
    desired_skill_coverage: float


# ---------------------------------------------------------------------------
# Output layer
# ---------------------------------------------------------------------------


class ShortlistResult(BaseModel, frozen=True):
    """Success case: ranked candidate shortlist."""

    role_id: str
    ranked_assessments: list[CandidateAssessment]  # top K, sorted by combined_score desc
    total_eligible: int  # size of pool before ranking
    exclusion_log: ExclusionLog
    config_snapshot: dict[str, Any]  # weights, K, model IDs for reproducibility


class NearMiss(BaseModel, frozen=True):
    """A candidate that almost qualified."""

    candidate_email: str
    name: str
    reason: str  # why they didn't qualify
    gap_summary: str  # "free 2 weeks late" or "wrong city"
    # AD-096: LLM "why consider once the gap is resolved"; set only for the shown top-3, None
    # otherwise (beyond the cap, or on predictor error). Optional → existing constructions valid.
    selection_rationale: str | None = None


class NoMatchResult(BaseModel, frozen=True):
    """Failure case: no eligible candidates found."""

    role_id: str
    reason: str  # high-level: "no candidates passed location gate"
    near_misses: list[NearMiss]  # top 3 closest
    exclusion_log: ExclusionLog


# ---------------------------------------------------------------------------
# Ports (dependency-inversion interfaces; AD-091)
# ---------------------------------------------------------------------------


@runtime_checkable
class CandidateStore(Protocol):
    """The port the query pipeline depends on to materialise serving ``Candidate``s (§6.0/AD-091).

    ``dsm/match`` + ``dsm/index`` depend on this **interface only** — never on where candidates
    come from. The concrete gold-backed adapter (``GoldCandidateStore``) lives at the CLI
    composition root (the only layer allowed to import ``dsm/ingest/``) and is injected there, so
    the data source is swappable (gold today → DB later) and the import boundary holds by
    construction. ``candidate_id`` is the store key (HMAC(email), AD-067).
    """

    def get(self, candidate_ids: list[str]) -> list[Candidate]:
        """Return hydrated serving ``Candidate``s for the given ids (coverage adapter-defined)."""
        ...
