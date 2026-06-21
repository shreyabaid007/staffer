"""Role clarification — produces TargetProfileScorecard (§6.1, B-002 FR-1).

Two paths:
1. **Echo (deterministic):** when ``description`` is empty/whitespace or no LM is provided,
   the role's declared skills are echoed verbatim into the scorecard. No LLM call.
2. **LLM (bounded DSPy):** when ``description`` is non-empty and ``lm`` is provided, a
   temperature-0 DSPy signature refines hard/desired skills from the free text.
   Failure → fallback to echo + logged warning.

No candidate PII is involved (the demand side carries no PII, §7) — no redaction needed.
All LLM calls go through ``PseudonymisedLM`` (the caller provides the LM instance).
"""

from __future__ import annotations

import dspy
import structlog

from dsm.config import load_prompt
from dsm.models import (
    OpenRole,
    ProficiencyLevel,
    SkillDepth,
    SkillRequirement,
    TargetProfileScorecard,
)

_log = structlog.get_logger("dsm.match.clarify")

# ---------------------------------------------------------------------------
# DSPy signature
# ---------------------------------------------------------------------------


class ClarifySignature(dspy.Signature):
    """Refine a role's skill requirements from free-text constraints."""

    role_title: str = dspy.InputField()
    required_skills: str = dspy.InputField()
    description: str = dspy.InputField()
    hard_skills: str = dspy.OutputField(
        desc="semicolon-separated hard skill entries as 'name:proficiency'"
    )
    desired_skills: str = dspy.OutputField(desc="semicolon-separated desired skill names")
    clarification_notes: str = dspy.OutputField(
        desc="1-2 sentence summary of constraints derived from the description"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROFICIENCY_MAP = {p.value: p for p in ProficiencyLevel}


def _parse_hard_skills(raw: str) -> list[SkillRequirement]:
    """Parse 'name:proficiency; name:proficiency; ...' into SkillRequirements."""
    results: list[SkillRequirement] = []
    for entry in raw.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        if ":" in entry:
            name, prof_str = entry.rsplit(":", 1)
            prof = _PROFICIENCY_MAP.get(prof_str.strip().lower())
        else:
            name = entry
            prof = None
        results.append(
            SkillRequirement(
                name=name.strip().lower(),
                depth=SkillDepth.HARD,
                min_proficiency=prof,
            )
        )
    return results


def _parse_desired_skills(raw: str) -> list[SkillRequirement]:
    """Parse 'name; name; ...' into desired SkillRequirements."""
    results: list[SkillRequirement] = []
    for entry in raw.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        results.append(SkillRequirement(name=entry.lower(), depth=SkillDepth.DESIRED))
    return results


def _echo_scorecard(role: OpenRole) -> TargetProfileScorecard:
    """Deterministic echo: split role skills by depth."""
    return TargetProfileScorecard(
        role_id=role.role_id,
        hard_depth_skills=[s for s in role.required_skills if s.depth == SkillDepth.HARD],
        desired_skills=[s for s in role.required_skills if s.depth == SkillDepth.DESIRED],
        location=role.location,
        co_location_required=role.co_location_required,
        start_date=role.start_date,
        availability_window_days=14,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def clarify_role(
    role: OpenRole,
    *,
    lm: dspy.LM | None = None,
) -> TargetProfileScorecard:
    """Clarify an OpenRole into a TargetProfileScorecard.

    Args:
        role: the parsed open-role row.
        lm: an optional DSPy LM (must be PseudonymisedLM). When provided and
            ``role.description`` is non-empty, refines skills via the LLM path.

    Returns:
        A frozen scorecard with hard/desired skills, location, and timing.
    """
    desc = (role.description or "").strip()
    if not desc or lm is None:
        return _echo_scorecard(role)

    sig = ClarifySignature.with_instructions(load_prompt("clarify_role"))
    predictor = dspy.Predict(sig)

    skills_str = "; ".join(f"{s.name} ({s.depth.value})" for s in role.required_skills)

    try:
        with dspy.context(lm=lm):
            result = predictor(
                role_title=role.title,
                required_skills=skills_str,
                description=desc,
            )
        hard = _parse_hard_skills(result.hard_skills)
        desired = _parse_desired_skills(result.desired_skills)
        notes = str(result.clarification_notes).strip() or None
    except Exception as exc:
        _log.warning(
            "clarify.llm_failed",
            role_id=role.role_id,
            reason=type(exc).__name__,
        )
        return _echo_scorecard(role)

    return TargetProfileScorecard(
        role_id=role.role_id,
        hard_depth_skills=hard,
        desired_skills=desired,
        location=role.location,
        co_location_required=role.co_location_required,
        start_date=role.start_date,
        availability_window_days=14,
        clarification_notes=notes,
    )
