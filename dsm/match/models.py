"""Query-time intermediate contracts for the demand side (B-1; ee-query-architecture §6.1).

These are the typed outputs of demand-CSV parsing — the banner ``demand_as_of`` (needed by
the freshness guard, AD-087) plus the ordered ``OpenRole``s and a record of skipped rows. The
frozen domain types (``SkillDepth``, ``SkillRequirement``, ``OpenRole``) are **reused** from
``dsm.models`` — never redefined (one model per fact; ``docs/structure.md``).
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, field_validator

from dsm.models import EvidenceCitation, OpenRole, SkillRequirement


class OpenRolesBanner(BaseModel, frozen=True):
    """The parsed Open Roles CSV header banner (FR-1).

    ``demand_as_of`` is the as-of date the demand snapshot was authored; it drives the
    freshness guard against supply ``valid_as_of`` (AD-087). ``source_path`` records which
    file the batch came from, for lineage.
    """

    demand_as_of: date
    source_path: str


class DemandParseOutcome(BaseModel, frozen=True):
    """The full result of parsing one Open Roles CSV (FR-1).

    ``roles`` are ordered by ``Priority`` ascending; ``skipped`` holds one human-readable
    line per malformed row that was logged and dropped (never silently lost, NF-3-style).
    """

    banner: OpenRolesBanner
    roles: list[OpenRole]
    skipped: list[str] = Field(default_factory=list)


class ScorecardClarification(BaseModel, frozen=True):
    """The clarify LLM's output (b-002; §6.2) — a match-local DSPy output type, not a frozen model.

    The bounded clarify signature emits only the refined skill breakdown + free-text notes; the
    orchestrator merges these into a ``TargetProfileScorecard`` and supplies the gate fields
    (location / co-location / start date / window) from the parsed role, never the LLM (§6.2). The
    LLM cannot invent or relax a gate — it only sharpens the capability requirements.
    """

    hard_depth_skills: list[SkillRequirement] = Field(default_factory=list)
    desired_skills: list[SkillRequirement] = Field(default_factory=list)
    clarification_notes: str | None = None


class ScoreExtraction(BaseModel, frozen=True):
    """The score LLM's output (b-002; §6.8) — a match-local DSPy output type, sub-scores ONLY.

    The bounded scoring signature emits the two sub-scores, a short narrative, and cited evidence.
    **Python** computes ``combined_score`` (AD-030 weights), ``hard_skill_coverage`` (exact, no
    adjacency), ``desired_skill_coverage`` (adjacency partial credit), and the flags — the LLM
    never does arithmetic (tech.md rule 4). Citations are verified verbatim before use (AD-073).
    """

    skill_match_score: float = 0.0
    feedback_score: float = 0.0
    narrative: str = ""
    evidence: list[EvidenceCitation] = Field(default_factory=list)

    @field_validator("skill_match_score", "feedback_score")
    @classmethod
    def _clamp_sub_score(cls, value: float) -> float:
        """Clamp the LLM sub-scores into ``[0.0, 1.0]`` — degrade, don't drop (AD-030).

        The signature is bounded but the LLM is not constrained to the range; an out-of-bounds
        value (e.g. ``1.4`` or ``-0.1``) would propagate through the deterministic combine into
        ``combined_score`` and skew the final ranking. We **clamp** rather than reject so an
        otherwise-usable assessment (narrative + verified citations) is kept — a raising validator
        would surface as a ``ValidationError`` outside ``score_candidate``'s LLM-error handling,
        breaking the "one bad candidate must not kill the batch" rule (§6.8).
        """
        return max(0.0, min(1.0, value))
