"""Output-guard tests (c-009 FR-2/FR-3) — hermetic: pure-Python clamp + duck-typed grounding stub.

Score-bounds is deterministic and runs in ``make check``. The real ``bespoke_minicheck`` grounding
model runs only in ``make eval``; here we verify the grounding wiring (context metadata shape,
filter behaviour, fail-open) against a stub.
"""

from __future__ import annotations

from typing import Any

from dsm.guardrails.output_guard import build_grounding_guard, ground_narrative, validate_scores

# --- validate_scores (FR-2, deterministic) ---------------------------------------------------


def test_clamps_above_upper_bound() -> None:
    assert validate_scores(1.5, 0.5) == (1.0, 0.5)


def test_clamps_below_lower_bound() -> None:
    assert validate_scores(-0.1, 2.0) == (0.0, 1.0)


def test_leaves_in_range_untouched() -> None:
    assert validate_scores(0.7, 0.3) == (0.7, 0.3)


def test_clamps_both_bounds() -> None:
    assert validate_scores(9.9, -9.9) == (1.0, 0.0)


# --- grounding stubs -------------------------------------------------------------------------


class _FilterGuard:
    """Stub Guard mimicking ``on_fail=FILTER``: returns an outcome with filtered output."""

    def __init__(self, validated: str | None, passed: bool = True) -> None:
        self._validated = validated
        self._passed = passed
        self.metadata: dict[str, Any] | None = None

    def validate(self, text: str, metadata: dict[str, Any] | None = None) -> Any:
        self.metadata = metadata
        return type(
            "Outcome",
            (),
            {"validated_output": self._validated, "validation_passed": self._passed},
        )()


class _RaisingGuard:
    def validate(self, text: str, metadata: dict[str, Any] | None = None) -> Any:
        raise RuntimeError("grounding API unavailable")


def test_grounding_none_guard_is_noop() -> None:
    assert ground_narrative(None, "some prose", ["src"]) == "some prose"


def test_grounding_empty_narrative_is_noop() -> None:
    assert ground_narrative(_RaisingGuard(), "", ["src"]) == ""


def test_grounding_filters_ungrounded_sentences() -> None:
    guard = _FilterGuard("Grounded sentence.")
    out = ground_narrative(
        guard, "Grounded sentence. Fabricated sentence.", ["Grounded sentence."]
    )
    assert out == "Grounded sentence."


def test_grounding_passes_sources_as_context_metadata() -> None:
    """bespoke_minicheck expects a single ``context`` string, not a ``sources`` list."""
    guard = _FilterGuard("x")
    ground_narrative(guard, "x", ["skill a", "feedback b"])
    assert guard.metadata == {"context": "skill a\nfeedback b"}


def test_grounding_fails_open_on_error() -> None:
    """FR-3-AC-4: an advisory guard must never block — a guard error returns the original."""
    assert ground_narrative(_RaisingGuard(), "prose", ["src"]) == "prose"


def test_grounding_returns_empty_when_all_stripped_not_original() -> None:
    """The FILTER→empty case must NOT revert to the ungrounded original (the old bypass)."""
    assert ground_narrative(_FilterGuard("", passed=False), "ungrounded prose", ["src"]) == ""


def test_grounding_trip_without_output_returns_empty() -> None:
    """A trip that yields no ``validated_output`` still must not surface the ungrounded prose."""
    assert ground_narrative(_FilterGuard(None, passed=False), "ungrounded prose", ["src"]) == ""


# --- build_grounding_guard -------------------------------------------------------------------


def test_build_none_when_master_disabled() -> None:
    assert build_grounding_guard({"guardrails": {"enabled": False}}) is None


def test_build_none_when_grounding_disabled() -> None:
    cfg = {"guardrails": {"enabled": True, "output": {"grounding": {"enabled": False}}}}
    assert build_grounding_guard(cfg) is None


def test_build_degrades_gracefully_without_optional_dep() -> None:
    cfg = {"guardrails": {"enabled": True, "output": {"grounding": {"enabled": True}}}}
    guard = build_grounding_guard(cfg)
    try:
        import guardrails.hub  # type: ignore  # noqa: F401
    except Exception:
        assert guard is None
    else:  # pragma: no cover
        assert guard is None or hasattr(guard, "validate")
