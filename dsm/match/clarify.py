"""Role clarification (step 2; §6.2) — ``OpenRole`` → ``TargetProfileScorecard``.

Two paths, one entry point:
- **echo** (deterministic) when the role carries no free-text ``description`` — partition the
  parsed ``required_skills`` by depth straight into the scorecard. The always-available baseline.
- **bounded LLM** when free text is present — a typed DSPy ``Signature`` over ``PseudonymisedLM``
  (``temperature=0``, the only provider path) refines the skill breakdown and records constraints
  in ``clarification_notes``. The LM is injected as a ``predict`` callable (the ``enrich`` seam) so
  unit tests mock it — no live network in ``make check``.

The LLM **cannot** set a gate: ``location`` / ``co_location_required`` / ``start_date`` /
``availability_window_days`` always come from the parsed role + config, never the model (§6.2,
AD-002). An LLM error falls back to the echo scorecard + a logged warning — a clarify failure never
drops the role. **No redaction** — demand free text describes the role, not a candidate (§7).
"""

from __future__ import annotations

from collections.abc import Callable

import dspy
import structlog

from dsm.config import load_prompt
from dsm.match.models import ScorecardClarification
from dsm.models import OpenRole, SkillDepth, SkillRequirement, TargetProfileScorecard

_log = structlog.get_logger("dsm.match.clarify")

# Injected LLM seam: OpenRole → the refined skill breakdown (mocked in tests).
ClarifyPredictor = Callable[[OpenRole], ScorecardClarification]


class RoleClarification(dspy.Signature):
    """Refine an open role into a target scorecard (instructions in config/prompts)."""

    title: str = dspy.InputField()
    description: str = dspy.InputField()
    required_skills: list[SkillRequirement] = dspy.InputField()
    clarification: ScorecardClarification = dspy.OutputField()


def make_clarify_predictor(lm: dspy.LM) -> ClarifyPredictor:
    """Build the real clarify predictor over ``PseudonymisedLM`` (used by the CLI, not tests)."""
    sig = RoleClarification.with_instructions(load_prompt("role_clarification"))
    predictor = dspy.Predict(sig)

    def _predict(role: OpenRole) -> ScorecardClarification:
        with dspy.context(lm=lm):
            return predictor(
                title=role.title,
                description=role.description or "",
                required_skills=role.required_skills,
            ).clarification

    return _predict


def _echo(role: OpenRole) -> TargetProfileScorecard:
    """Deterministic baseline: partition the parsed required skills by depth (the Slice-0 stub)."""
    return TargetProfileScorecard(
        role_id=role.role_id,
        hard_depth_skills=[s for s in role.required_skills if s.depth == SkillDepth.HARD],
        desired_skills=[s for s in role.required_skills if s.depth == SkillDepth.DESIRED],
        location=role.location,
        co_location_required=role.co_location_required,
        start_date=role.start_date,
        availability_window_days=14,
    )


def clarify_role(
    role: OpenRole, *, predict: ClarifyPredictor | None = None
) -> TargetProfileScorecard:
    """Clarify a role into a scorecard — echo when there's no free text, else a bounded LLM refine.

    Args:
        role: the parsed open role.
        predict: the injected LLM seam. ``None`` (the default / tests without an LM) → echo only.

    Returns:
        A ``TargetProfileScorecard``. When ``predict`` runs, only the skill breakdown +
        ``clarification_notes`` come from the LLM; the gate fields come from ``role`` (§6.2). An
        LLM error falls back to the echo scorecard.
    """
    if predict is None or not (role.description and role.description.strip()):
        return _echo(role)

    try:
        clarification = predict(role)
    except Exception as exc:  # noqa: BLE001 — never drop the role on a clarify failure (§6.2)
        _log.warning(
            "clarify.failed_fallback_echo", role_id=role.role_id, reason=type(exc).__name__
        )
        return _echo(role)

    return TargetProfileScorecard(
        role_id=role.role_id,
        hard_depth_skills=clarification.hard_depth_skills,
        desired_skills=clarification.desired_skills,
        location=role.location,
        co_location_required=role.co_location_required,
        start_date=role.start_date,
        availability_window_days=14,
        clarification_notes=clarification.clarification_notes,
    )
