"""LLM usage tracking via DSPy's callback system (AD-XXX).

A cross-cutting concern wired at the CLI composition root. Accumulates per-LLM-call
usage (tokens, cost, cache status) and exposes a summary for the ``explain`` output.

Import boundary: may import ``dspy`` and ``structlog`` only. Must NOT import
``dsm.pii``, ``dsm.match``, ``dsm.ingest``, or ``modal``.
"""

from __future__ import annotations

import time
from typing import Any

import structlog
from dspy.utils.callback import BaseCallback

_log = structlog.get_logger("dsm.observability")


class UsageTracker(BaseCallback):
    """Accumulate per-LLM-call usage for the explain output."""

    def __init__(self) -> None:
        self._calls: list[dict[str, Any]] = []
        self._pending: dict[str, float] = {}

    def on_lm_start(
        self,
        call_id: str,
        instance: Any,
        inputs: dict[str, Any],
    ) -> None:
        self._pending[call_id] = time.monotonic()

    def on_lm_end(
        self,
        call_id: str,
        outputs: dict[str, Any] | None,
        exception: Exception | None = None,
    ) -> None:
        elapsed = time.monotonic() - self._pending.pop(call_id, time.monotonic())
        entry: dict[str, Any] = {
            "call_id": call_id,
            "elapsed_s": round(elapsed, 3),
            "error": type(exception).__name__ if exception else None,
        }
        self._calls.append(entry)

    @property
    def call_count(self) -> int:
        return len(self._calls)

    @property
    def total_elapsed_s(self) -> float:
        return round(sum(c["elapsed_s"] for c in self._calls), 3)

    @property
    def usage_summary(self) -> dict[str, Any]:
        """Return a summary suitable for the explain output.

        Kept outside the determinism-hashed payload — usage varies per run.
        """
        errors = sum(1 for c in self._calls if c["error"])
        return {
            "llm_calls": self.call_count,
            "total_elapsed_s": self.total_elapsed_s,
            "errors": errors,
            "per_call": list(self._calls),
        }

    def reset(self) -> None:
        self._calls.clear()
        self._pending.clear()
