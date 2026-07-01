"""Narrative guard — bias + toxicity screening of human-facing text (c-009, FR-4).

Composes the Guardrails AI ``guardrails/bias_check`` (age/gender/ethnicity bias) and
``guardrails/toxic_language`` (toxic/unprofessional language) hub validators — both **local** ML
models — into one guard applied to every human-facing generated text: the scoring narrative AND the
near-miss rationale (FR-4-AC-5).

**On-fail = withhold (the design's sanctioned fallback for REASK).** A full DSPy reask would recall
the LLM *through the PII boundary*, which is out of scope for this slice; instead, on a detected
bias/toxicity trip :func:`validate_narrative` **withholds** the offending text (replaces it with a
neutral notice) and logs it — a staffing product must never surface biased or toxic language to a
human. It distinguishes a **detection** (``ValidationError`` from ``on_fail=EXCEPTION`` → withhold)
from an **infrastructure error** (a model crash / load failure → **fail-open**: keep the narrative,
log the error) so a transient model glitch cannot blank every narrative in a shortlist. A reask
through ``PseudonymisedLM`` is a documented follow-up.

Guardrails AI is imported lazily; the guard degrades to ``None`` (no-op) when disabled/unavailable.
Each validator is attached in its own try-block, so one uninstalled validator (e.g. ToxicLanguage)
never discards a working sibling (e.g. BiasCheck).
"""

from __future__ import annotations

from typing import Any

import structlog

_log = structlog.get_logger("dsm.guardrails.narrative_guard")

_DEFAULT_BIAS_THRESHOLD = 0.9
_DEFAULT_TOXICITY_THRESHOLD = 0.5

# Human-facing replacement when a narrative fails screening — never show the offending text.
NARRATIVE_WITHHELD_NOTICE = "[narrative withheld pending bias/toxicity review]"


def build_narrative_guard(config: dict[str, Any]) -> Any | None:
    """Build the composed bias + toxicity narrative guard; ``None`` if disabled/unavailable.

    Attaches only the enabled validators (``guardrails.narrative.bias_check`` and/or
    ``guardrails.narrative.toxicity``), each with ``on_fail=EXCEPTION`` so
    :func:`validate_narrative` can catch a trip and withhold the text. Returns ``None`` when the
    master toggle is off, both validators are off, or the framework/validator is unavailable.
    """
    narrative = _narrative_config(config)
    if narrative is None:
        return None
    bias_on = narrative.get("bias_check", {}).get("enabled", False)
    toxicity_on = narrative.get("toxicity", {}).get("enabled", False)
    if not (bias_on or toxicity_on):
        return None
    try:
        from guardrails import Guard, OnFailAction  # type: ignore  # optional extra
    except Exception as exc:  # noqa: BLE001 — optional extra not installed
        _log.warning("guardrails.narrative_unavailable", reason=type(exc).__name__)
        return None

    # Attach each validator independently: an uninstalled ToxicLanguage must not discard a working
    # BiasCheck (and vice-versa). ``attached`` tracks whether at least one validator was wired.
    guard = Guard()
    attached = False
    if bias_on:
        try:
            from guardrails.hub import BiasCheck  # type: ignore  # installed via hub

            threshold = float(narrative["bias_check"].get("threshold", _DEFAULT_BIAS_THRESHOLD))
            guard = guard.use(BiasCheck, threshold=threshold, on_fail=OnFailAction.EXCEPTION)
            attached = True
        except Exception as exc:  # noqa: BLE001 — validator not installed
            _log.warning("guardrails.bias_check_unavailable", reason=type(exc).__name__)
    if toxicity_on:
        try:
            from guardrails.hub import ToxicLanguage  # type: ignore  # installed via hub

            threshold = float(narrative["toxicity"].get("threshold", _DEFAULT_TOXICITY_THRESHOLD))
            guard = guard.use(
                ToxicLanguage,
                threshold=threshold,
                validation_method="sentence",
                on_fail=OnFailAction.EXCEPTION,
            )
            attached = True
        except Exception as exc:  # noqa: BLE001 — validator not installed
            _log.warning("guardrails.toxic_language_unavailable", reason=type(exc).__name__)
    return guard if attached else None


def _narrative_config(config: dict[str, Any]) -> dict[str, Any] | None:
    """Return the ``narrative`` sub-config when the master toggle is on, else None."""
    guardrails = config.get("guardrails", {})
    if not guardrails.get("enabled", False):
        return None
    return guardrails.get("narrative", {})


def validate_narrative(guard: Any | None, narrative: str) -> str:
    """Screen ``narrative`` for bias + toxicity (FR-4). Return it, or a withheld notice on a trip.

    No-op when ``guard`` is ``None`` or ``narrative`` is empty. On a detected bias/toxicity trip (a
    ``ValidationError`` from ``on_fail=EXCEPTION``), log ``guardrails.narrative_screened`` + return
    :data:`NARRATIVE_WITHHELD_NOTICE` — the offending text is never surfaced, and the log records
    **no** narrative content. On an **infrastructure error** (model load/crash, not a detection),
    **fail open**: log ``guardrails.narrative_guard_error`` and return the original narrative, so a
    transient glitch cannot blank every narrative in a shortlist.

    Args:
        guard: the guard from :func:`build_narrative_guard`, or ``None``.
        narrative: the human-facing generated text (scoring narrative or near-miss rationale).

    Returns:
        The original narrative when it passes or the guard errors, else the withheld notice.
    """
    if guard is None or not narrative:
        return narrative
    try:
        guard.validate(narrative)
    except Exception as exc:  # noqa: BLE001 — separate a real detection from an infra failure
        # Guardrails raises ``guardrails.errors.ValidationError`` on an ``on_fail=EXCEPTION`` trip;
        # match by class name (the optional dep is never imported here). Anything else is an infra
        # error → fail open. (Fail-*closed* on a detection: never surface unscreened text.)
        if type(exc).__name__ == "ValidationError":
            _log.warning("guardrails.narrative_screened")
            return NARRATIVE_WITHHELD_NOTICE
        _log.warning("guardrails.narrative_guard_error", reason=type(exc).__name__)
        return narrative
    return narrative
