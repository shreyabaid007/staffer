"""Output guard — score-bounds enforcement + narrative grounding (c-009, FR-2/FR-3).

Two independent output validators applied to the LLM's ``ScoreExtraction``:

- **Score bounds (FR-2)** — :func:`validate_scores` clamps ``skill_match``/``feedback`` into
  ``[0.0, 1.0]``. Deterministic, no LLM, negligible latency, always on → part of ``make check``.
  This is a **regression tripwire**, not new runtime protection: the frozen ``ScoreExtraction``
  field validator (AD-030) already clamps at construction, so under current code this never fires
  — it exists so that if AD-030's clamp is ever removed/changed, the guard layer (and its test)
  still enforce the bound. We deliberately do **not** use ``guardrails/valid_range`` for a trivial
  float clamp: a hub-installed validator in the commit gate would add a network dependency for zero
  added protection (AD-XXX).

- **Grounding (FR-3)** — :func:`build_grounding_guard` / :func:`ground_narrative` wrap
  ``bespokelabs/bespoke_minicheck`` to filter narrative *prose* sentences unsupported by the
  candidate's source context. Complements the verbatim citation check (AD-073), which covers quoted
  evidence, not prose. Uses a **remote** API (needs ``BESPOKE_API_KEY``) → eval-tier; degrades to a
  logged no-op when disabled/unavailable, and is **fail-open** (never blocks the response, FR-3).
  **PII boundary (Rule 3):** because the API is remote, :func:`ground_narrative` must only ever be
  handed **pseudonymised** text — the composition-root caller redacts + leak-scans the narrative
  and sources first (this module never imports ``dsm.pii``).
"""

from __future__ import annotations

from typing import Any

import structlog

_log = structlog.get_logger("dsm.guardrails.output_guard")

_DEFAULT_GROUNDING_THRESHOLD = 0.5


def validate_scores(skill: float, feedback: float) -> tuple[float, float]:
    """Clamp sub-scores into ``[0.0, 1.0]`` — a regression tripwire for the bound (FR-2).

    Under current code this is a no-op: the frozen ``ScoreExtraction`` field validator (AD-030)
    already clamps at construction, so the values reaching here are always in range and the
    ``score_clamped`` warning never fires. It is kept (and unit-tested with ``1.5 → 1.0``) so that
    if AD-030's clamp is ever removed the guard layer still enforces the bound and the correction
    becomes observable.

    Args:
        skill: the ``skill_match`` sub-score.
        feedback: the ``feedback`` sub-score.

    Returns:
        ``(clamped_skill, clamped_feedback)`` — each in ``[0.0, 1.0]``.
    """
    clamped_skill = max(0.0, min(1.0, skill))
    clamped_feedback = max(0.0, min(1.0, feedback))
    if clamped_skill != skill:
        _log.warning(
            "guardrails.score_clamped", field="skill_match", original=skill, clamped=clamped_skill
        )
    if clamped_feedback != feedback:
        _log.warning(
            "guardrails.score_clamped",
            field="feedback",
            original=feedback,
            clamped=clamped_feedback,
        )
    return clamped_skill, clamped_feedback


def build_grounding_guard(config: dict[str, Any]) -> Any | None:
    """Build the grounding guard (``bespoke_minicheck``); ``None`` if disabled/unavailable.

    Enabled only when ``guardrails.enabled`` and ``guardrails.output.grounding.enabled`` are true
    and the framework + hub validator import cleanly. ``on_fail=FILTER`` so ungrounded sentences
    are stripped rather than the whole response rejected (FR-3-AC-4).
    """
    cfg = _grounding_config(config)
    if cfg is None:
        return None
    try:
        from guardrails import Guard, OnFailAction  # type: ignore  # optional extra
        from guardrails.hub import BespokeMiniCheck  # type: ignore  # installed via hub
    except Exception as exc:  # noqa: BLE001 — optional dep / uninstalled hub validator
        _log.warning("guardrails.grounding_unavailable", reason=type(exc).__name__)
        return None
    threshold = float(cfg.get("threshold", _DEFAULT_GROUNDING_THRESHOLD))
    return Guard().use(
        BespokeMiniCheck, threshold=threshold, split_sentences=True, on_fail=OnFailAction.FILTER
    )


def _grounding_config(config: dict[str, Any]) -> dict[str, Any] | None:
    """Return the ``grounding`` sub-config when enabled (master + validator), else None."""
    guardrails = config.get("guardrails", {})
    if not guardrails.get("enabled", False):
        return None
    grounding = guardrails.get("output", {}).get("grounding", {})
    return grounding if grounding.get("enabled", False) else None


def ground_narrative(guard: Any | None, narrative: str, sources: list[str]) -> str:
    """Filter narrative sentences unsupported by ``sources`` (``bespoke_minicheck``, FR-3).

    ``narrative`` and ``sources`` MUST already be pseudonymised (the caller redacts + leak-scans —
    this validator can be remote, Rule 3). ``sources`` are joined into the single ``context``
    string the validator expects (metadata key ``"context"``, not ``sources``). Complements the
    citation check (AD-073) — it covers prose claims, not quoted evidence.

    **Never blocks (FR-3-AC-4)** but never silently no-ops on the case it exists for:
    - guard **error** → fail-open: return the input narrative (advisory; citations load-bearing);
    - guard **passed, nothing filtered** → return the input unchanged;
    - guard **filtered** some/all sentences → return the *filtered* text verbatim, **even when
      empty** (a fully-ungrounded narrative collapses to ``""`` rather than reverting to the
      ungrounded original — the earlier revert-to-original path defeated the guard).

    Args:
        guard: the guard from :func:`build_grounding_guard`, or ``None``.
        narrative: the (pseudonymised) LLM narrative to check.
        sources: the (pseudonymised) candidate source facts to check against.

    Returns:
        The grounded narrative (ungrounded sentences removed; ``""`` if none were grounded).
    """
    if guard is None or not narrative:
        return narrative
    context = "\n".join(s for s in sources if s)
    try:
        outcome = guard.validate(narrative, metadata={"context": context})
    except Exception as exc:  # noqa: BLE001 — advisory; never drop the narrative on a guard error
        _log.warning("guardrails.grounding_error", reason=type(exc).__name__)
        return narrative
    validated = getattr(outcome, "validated_output", None)
    passed = getattr(outcome, "validation_passed", True)
    if isinstance(validated, str):
        if validated != narrative:
            _log.info("guardrails.narrative_grounded", empty=(not validated))
        return validated  # filtered text — respected verbatim, even when empty
    if not passed:
        # Guard tripped but produced no usable output → do NOT surface ungrounded prose.
        _log.info("guardrails.narrative_grounded", empty=True)
        return ""
    return narrative  # passed with no filtered output → unchanged
