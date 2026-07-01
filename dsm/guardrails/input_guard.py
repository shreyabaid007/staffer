"""Input guard — prompt-injection / jailbreak detection on untrusted text (c-009, FR-1).

Wraps the Guardrails AI ``guardrails/detect_jailbreak`` hub validator. Runs **before** the PII
redaction pass and **before** any LLM call (FR-1-AC-4) at the composition root — never inside
``PseudonymisedLM`` (the PII boundary) or ``dsm.match`` (the deterministic spine).

The validator runs a **local** HuggingFace classifier on the orchestrator (no network, no data
leaving the box), so it seeing the candidate's de-anonymised text does not breach the PII boundary
— exactly like the local Presidio NER pass. A rejection logs the ``candidate_id`` **only**, never
the offending text (PII- and adversarial-content-safe).

Guardrails AI is imported **lazily**: this module is importable without the package, and when the
framework or the hub validator is unavailable :func:`build_input_guard` degrades to ``None`` (a
logged no-op) — the guard is defence-in-depth, never load-bearing for correctness.
"""

from __future__ import annotations

from typing import Any

import structlog

_log = structlog.get_logger("dsm.guardrails.input_guard")

# Guardrails' default detect_jailbreak score threshold (live validator source, mid-2026).
_DEFAULT_JAILBREAK_THRESHOLD = 0.81


class InputRejectedError(Exception):
    """Raised when untrusted text fails the injection guard.

    Carries the ``candidate_id`` for observability and **never** the text content — the message is
    safe to log and surface. The caller (the guarded predictor / the ingest loop) catches this and
    skips the candidate rather than sending adversarial text to the LLM.
    """

    def __init__(self, candidate_id: str) -> None:
        super().__init__(f"input rejected by injection guard for candidate {candidate_id}")
        self.candidate_id = candidate_id


def build_input_guard(config: dict[str, Any]) -> Any | None:
    """Build the jailbreak/injection input guard from ``config``; ``None`` if disabled/unavailable.

    Returns a Guardrails ``Guard`` configured with ``DetectJailbreak(on_fail=EXCEPTION)`` when both
    ``guardrails.enabled`` and ``guardrails.input.jailbreak_detection.enabled`` are true and the
    framework + hub validator import cleanly. Any import failure (package or hub validator not
    installed) degrades to ``None`` with a one-line warning.

    Args:
        config: the loaded runtime config (reads the ``guardrails`` section).

    Returns:
        A ``Guard`` whose ``.validate(text)`` raises on a detected injection, or ``None`` (no-op).
    """
    cfg = _jailbreak_config(config)
    if cfg is None:
        return None
    try:
        from guardrails import Guard, OnFailAction  # type: ignore  # optional extra
        from guardrails.hub import DetectJailbreak  # type: ignore  # installed via hub
    except Exception as exc:  # noqa: BLE001 — optional dep / uninstalled hub validator
        _log.warning("guardrails.input_guard_unavailable", reason=type(exc).__name__)
        return None
    threshold = float(cfg.get("threshold", _DEFAULT_JAILBREAK_THRESHOLD))
    return Guard().use(DetectJailbreak, threshold=threshold, on_fail=OnFailAction.EXCEPTION)


def _jailbreak_config(config: dict[str, Any]) -> dict[str, Any] | None:
    """Return the ``jailbreak_detection`` sub-config when enabled (master + flag), else None."""
    guardrails = config.get("guardrails", {})
    if not guardrails.get("enabled", False):
        return None
    jailbreak = guardrails.get("input", {}).get("jailbreak_detection", {})
    return jailbreak if jailbreak.get("enabled", False) else None


def validate_input(guard: Any | None, text: str, candidate_id: str) -> None:
    """Validate untrusted ``text`` before it reaches the LLM (FR-1-AC-1/2).

    No-op when ``guard`` is ``None`` (disabled/unavailable) or ``text`` is empty. On any guard trip
    or error, log ``guardrails.input_rejected`` with ``candidate_id`` **only** and raise
    :class:`InputRejectedError`. Fail-closed by design: an adversarial or unverifiable input is
    rejected, never sent onward (FR-1 is a P0 boundary).

    Args:
        guard: the guard from :func:`build_input_guard`, or ``None``.
        text: the untrusted candidate text (resume / feedback / profile summary).
        candidate_id: the pseudonymous id, for the PII-safe rejection log.

    Raises:
        InputRejectedError: when the guard detects an injection (or errors).
    """
    if guard is None or not text:
        return
    try:
        guard.validate(text)
    except Exception:  # noqa: BLE001 — any trip/error → reject; never log or forward the text
        _log.warning("guardrails.input_rejected", candidate_id=candidate_id)
        raise InputRejectedError(candidate_id) from None
