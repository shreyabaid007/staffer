"""Web-local request/response contracts (c-008; AD-XXX).

API-layer Pydantic models — the ``RoleIntake`` / ``ScorecardClarification`` precedent: module-local
view/transport types, **never** added to the frozen ``dsm.models`` (AD-060). The match response is
a *view* wrapping the rendered ``ShortlistResult`` / ``NoMatchResult`` that also carries the
pseudonymous ``candidate_id`` per candidate (captured pre-render) — a stable handle for ``/resume``
+ ``/decisions`` while showing real identity. No frozen-contract change → no
``make contract-snapshot``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

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
