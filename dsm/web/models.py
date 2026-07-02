"""Web-local request/response contracts (c-008; AD-XXX).

API-layer Pydantic models — the ``RoleIntake`` / ``ScorecardClarification`` precedent: module-local
view/transport types, **never** added to the frozen ``dsm.models`` (AD-060). The match response is
a *view* wrapping the rendered ``ShortlistResult`` / ``NoMatchResult`` that also carries the
pseudonymous ``candidate_id`` per candidate (captured pre-render) — a stable handle for ``/resume``
+ ``/decisions`` while showing real identity. No frozen-contract change → no
``make contract-snapshot``.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Natural-language door — /intake (parse+echo) and /match/query (confirm+run)
# ---------------------------------------------------------------------------


class Clarifications(BaseModel):
    """Operator answers to a bounded clarification round (city / "remote" or ISO date)."""

    location: str | None = None  # a city name, or "remote"
    start: str | None = None  # ISO YYYY-MM-DD


class IntakeRequest(BaseModel):
    """Body for both ``/intake`` (parse+echo) and ``/match/query`` (confirm+run)."""

    prose: str
    clarifications: Clarifications | None = None
    confirm: bool = False  # /match/query requires True (the gate is the confirmed role, AD-110)


class RoleEcho(BaseModel):
    """How the role was read — display only. ``co_location_required`` is server-derived (AD-002).

    Mirrors the CLI ``_echo_role`` so a relative-date or negation misparse is caught before gating.
    """

    role_id: str
    title: str
    location: str  # "Chennai" | "remote (India)" | "any (distributed)"
    co_location_required: bool
    exclude_cities: list[str] = Field(default_factory=list)
    start_date: str  # ISO
    start_phrase: str | None = None
    hard_skills: list[str] = Field(default_factory=list)
    desired_skills: list[str] = Field(default_factory=list)
    notes: str | None = None


class IntakeResponse(BaseModel):
    """Result of ``/intake``: a ready role echo, or the missing required gate fields to clarify."""

    status: Literal["ready", "needs_clarification"]
    role_id: str
    echo: RoleEcho | None = None  # present when status == "ready"
    missing: list[str] = Field(default_factory=list)  # present when needs_clarification


# ---------------------------------------------------------------------------
# CSV / demand-sheet door — /demand/parse (picker) and /match/role (run)
# ---------------------------------------------------------------------------


class RoleSummary(BaseModel):
    """One parsed demand row for the role picker."""

    role_id: str
    title: str
    location: str
    start_date: str  # ISO
    co_location_required: bool


class DemandParseResponse(BaseModel):
    """Parsed Open Roles CSV: banner date, ordered roles, and skipped-row lines (never dropped)."""

    demand_as_of: str  # ISO
    roles: list[RoleSummary] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Shortlist view (both doors) — wraps the de-anonymised result + the stable candidate_id
# ---------------------------------------------------------------------------


class FlagView(BaseModel):
    """A surfaced trade-off (type + message)."""

    type: str
    message: str


class EvidenceView(BaseModel):
    """A cited verbatim quote and its source (resume / feedback / supply sheet)."""

    source: str
    text: str


class CandidateView(BaseModel):
    """A shortlisted candidate. ``candidate_id`` is the pseudonym (URL/log-safe, AD-067) for
    ``/resume`` + ``/decisions``; ``name`` / ``email`` are de-anonymised (post-render, AD-107).
    ``has_resume`` reflects a ``PROFILE_PDF`` citation in gold (drives the drawer affordance).
    """

    candidate_id: str
    name: str
    email: str
    source: str
    location: str
    availability: str
    years_experience: int | None = None
    has_resume: bool = False


class AssessmentView(BaseModel):
    """One ranked assessment: sub-scores, flags, evidence, and narrative — nothing summarised."""

    candidate: CandidateView
    skill_match_score: float
    feedback_score: float
    combined_score: float
    hard_skill_coverage: float
    desired_skill_coverage: float
    flags: list[FlagView] = Field(default_factory=list)
    evidence: list[EvidenceView] = Field(default_factory=list)
    narrative: str = ""


class NearMissView(BaseModel):
    """A near-miss / closest-on-skills row (one fixable decision away, or a hard skill short)."""

    candidate_id: str
    name: str
    reason: str
    gap_summary: str
    selection_rationale: str | None = None


class ExclusionView(BaseModel):
    """A gate / exact-filter exclusion (who was dropped and why) — the transparency layer.

    ``candidate_id`` is the pseudonym (for ``/resume``); ``display`` is the de-anonymised label
    (real email — ``Exclusion`` carries no name field), or the ``candidate_id`` on a vault miss.
    """

    candidate_id: str
    display: str
    reason: str
    detail: str


class MatchResponse(BaseModel):
    """The full match result for the UI — shortlist OR no-match, with all explainability fields."""

    role_id: str
    run_id: str  # synthesized per match, for /decisions keying
    outcome: Literal["shortlist", "no_match"]
    shortlist: list[AssessmentView] = Field(default_factory=list)
    no_match_reason: str | None = None
    near_misses: list[NearMissView] = Field(default_factory=list)
    closest_on_skills: list[NearMissView] = Field(default_factory=list)
    exclusions: list[ExclusionView] = Field(default_factory=list)
    total_eligible: int | None = None
    config_snapshot: dict | None = None


# ---------------------------------------------------------------------------
# Decision capture (append-only; never feeds ranking)
# ---------------------------------------------------------------------------


class DecisionItem(BaseModel):
    """One human decision on a candidate, keyed by the pseudonymous ``candidate_id``."""

    candidate_id: str
    action: Literal["forward", "set_aside"]
    reason: str | None = None


class DecisionRequest(BaseModel):
    """A batch of decisions for one match run (``run_id``)."""

    run_id: str
    role_id: str
    reviewer: str
    decisions: list[DecisionItem] = Field(default_factory=list)


class DecisionResponse(BaseModel):
    """Confirmation of an append-only decision write."""

    recorded: int
    run_id: str


# ---------------------------------------------------------------------------
# Supply management (c-011; AD-XXY) — the raw sheets are the source of truth
# ---------------------------------------------------------------------------

Category = Literal["beach", "rolling_off", "new_joiner"]


class SupplyRowView(BaseModel):
    """One supply-sheet row + its pipeline sync status (gold/silver-derived)."""

    candidate_id: str  # pseudonym (HMAC) — stable status-join handle, never shown as identity
    name: str
    email: str
    grade: str | None = None
    skills: list[str] = Field(default_factory=list)
    location: str | None = None
    chennai_open: bool = False
    category: Category
    roll_off_date: str | None = None  # rolling_off
    confidence: str | None = None  # rolling_off: high | medium | low
    join_date: str | None = None  # new_joiner
    days_on_beach: int | None = None  # beach
    notes: str | None = None
    ingested: bool = False  # a live (non-tombstoned) gold entity exists
    has_resume: bool = False  # silver/gold link, or a web-uploaded PDF awaiting ingest
    feedback_count: int = 0  # entries on the gold entity (post-ingest truth)
    feedback_files: list[str] = Field(default_factory=list)  # raw .md files linked by email


class SupplySheetView(BaseModel):
    """One category sheet: banner date + rows + skipped-row notes (never silently dropped)."""

    category: Category
    as_of: str | None = None  # ISO, from the banner
    rows: list[SupplyRowView] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)


class SupplyResponse(BaseModel):
    """``GET /supply`` — all three category sheets."""

    sheets: list[SupplySheetView] = Field(default_factory=list)


class AddCandidateRequest(BaseModel):
    """``POST /supply/candidates`` — category-specific required fields enforced (FR-2-AC-4)."""

    category: Category
    name: str = Field(min_length=1)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    grade: str | None = None
    skills: list[str] = Field(default_factory=list)
    location: str | None = None
    chennai_open: bool = False
    roll_off_date: date | None = None
    confidence: Literal["high", "medium", "low"] | None = None
    join_date: date | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _category_fields(self) -> AddCandidateRequest:
        if self.category == "rolling_off" and (self.roll_off_date is None or not self.confidence):
            raise ValueError("rolling_off requires roll_off_date and confidence")
        if self.category == "new_joiner" and self.join_date is None:
            raise ValueError("new_joiner requires join_date")
        return self


class FeedbackWriteRequest(BaseModel):
    """``POST /supply/candidates/{email}/feedback`` — written-Markdown variant."""

    text: str = Field(min_length=1)
    source: Literal["internal_ee", "client"] = "internal_ee"


class AttachmentResponse(BaseModel):
    """Result of a resume/feedback store: the filename + whether it will link at ingest."""

    stored: str
    link_check: Literal["ok", "no_email_found", "unknown"]


# ---------------------------------------------------------------------------
# Ingest trigger (c-011; FR-4) — one button, background job, polled status
# ---------------------------------------------------------------------------


class IngestSummaryView(BaseModel):
    """Structured counts parsed from the pipeline's PII-safe summary lines."""

    landed: int = 0
    skipped: int = 0
    gold_updated: int = 0
    gold_unchanged: int = 0
    enrich_llm_calls: int = 0
    enrich_cache_hits: int = 0
    tombstoned: int = 0
    revived: int = 0
    indexed: int = 0
    index_skipped_unchanged: int = 0
    index_removed: int = 0


class IngestStatusResponse(BaseModel):
    """``GET /ingest/status`` (and the ``POST /ingest/run`` 202 body)."""

    state: Literal["idle", "running", "succeeded", "failed"]
    job_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    summary: IngestSummaryView | None = None
    log_tail: list[str] = Field(default_factory=list)
