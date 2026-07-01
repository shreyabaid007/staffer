"""Narrative-guard tests (c-009 FR-4) — hermetic: duck-typed stub guards, no ``guardrails`` import.

The real ``bias_check`` / ``toxic_language`` models run only in ``make eval``. Here we verify the
withhold-on-trip contract: a clean narrative is transparent, a tripped guard yields the neutral
withheld notice (never the offending text), and the builder no-ops when disabled/unavailable.
"""

from __future__ import annotations

from typing import Any

from dsm.guardrails.narrative_guard import (
    NARRATIVE_WITHHELD_NOTICE,
    build_narrative_guard,
    validate_narrative,
)


class ValidationError(Exception):
    """Mimics ``guardrails.errors.ValidationError`` — matched by class name (a real detection)."""


class _PassGuard:
    def validate(self, text: str) -> None:
        return None


class _TripGuard:
    """Stub mimicking ``on_fail=EXCEPTION`` on a detection (raises a ValidationError-named exc)."""

    def validate(self, text: str) -> None:
        raise ValidationError("bias detected")


class _CrashGuard:
    """Stub mimicking an infrastructure error (model load/crash), NOT a detection."""

    def validate(self, text: str) -> None:
        raise RuntimeError("model failed to load")


def test_clean_narrative_passes_through() -> None:
    assert validate_narrative(_PassGuard(), "Strong delivery record.") == "Strong delivery record."


def test_bias_or_toxicity_is_withheld() -> None:
    assert validate_narrative(_TripGuard(), "some biased narrative") == NARRATIVE_WITHHELD_NOTICE


def test_withheld_notice_does_not_leak_the_text() -> None:
    offending = "OFFENDING-BIASED-TEXT-42"
    assert offending not in validate_narrative(_TripGuard(), offending)


def test_infra_error_fails_open_keeps_narrative() -> None:
    """A model crash (not a detection) must NOT blank the narrative — fail open."""
    assert validate_narrative(_CrashGuard(), "clean narrative") == "clean narrative"


def test_none_guard_is_noop() -> None:
    assert validate_narrative(None, "narrative") == "narrative"


def test_empty_narrative_is_noop() -> None:
    assert validate_narrative(_TripGuard(), "") == ""


def _cfg(*, enabled: bool = True, bias: bool = True, toxicity: bool = True) -> dict[str, Any]:
    return {
        "guardrails": {
            "enabled": enabled,
            "narrative": {
                "bias_check": {"enabled": bias},
                "toxicity": {"enabled": toxicity},
            },
        }
    }


def test_build_none_when_master_disabled() -> None:
    assert build_narrative_guard(_cfg(enabled=False)) is None


def test_build_none_when_both_validators_disabled() -> None:
    assert build_narrative_guard(_cfg(bias=False, toxicity=False)) is None


def test_build_degrades_gracefully_without_optional_dep() -> None:
    guard = build_narrative_guard(_cfg())
    try:
        import guardrails.hub  # type: ignore  # noqa: F401
    except Exception:
        assert guard is None
    else:  # pragma: no cover
        assert guard is None or hasattr(guard, "validate")
