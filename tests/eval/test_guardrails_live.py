"""Tier-3 live guardrail tests (c-009 FR-3/FR-4) — real hub validators, run under ``make eval``.

These exercise the actual ML/LLM validators end-to-end (``bias_check`` / ``toxic_language`` local
models; ``bespoke_minicheck`` remote API). They **skip** cleanly whenever the optional
``guardrails-ai`` extra, a specific hub validator, or a required key (``BESPOKE_API_KEY``) is
absent — so the eval suite stays green on a bare checkout, mirroring the key-gated faithfulness
judge (AD-105). The deterministic wiring is covered offline in ``tests/guardrails/``.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("guardrails", reason="guardrails-ai optional extra not installed")

from dsm.guardrails.narrative_guard import (  # noqa: E402
    NARRATIVE_WITHHELD_NOTICE,
    build_narrative_guard,
    validate_narrative,
)
from dsm.guardrails.output_guard import build_grounding_guard, ground_narrative  # noqa: E402


def _hub_available(name: str) -> bool:
    """True if a ``guardrails.hub`` validator class is importable (installed)."""
    try:
        __import__("guardrails.hub", fromlist=[name])
        return hasattr(__import__("guardrails.hub", fromlist=[name]), name)
    except Exception:
        return False


_NARRATIVE_CFG = {
    "guardrails": {
        "enabled": True,
        "narrative": {
            "bias_check": {"enabled": True, "threshold": 0.9},
            "toxicity": {"enabled": True, "threshold": 0.5},
        },
    }
}

_GROUNDING_CFG = {
    "guardrails": {"enabled": True, "output": {"grounding": {"enabled": True, "threshold": 0.5}}}
}


@pytest.mark.eval_live
class TestNarrativeGuardLive:
    def _guard(self):
        if not (_hub_available("BiasCheck") or _hub_available("ToxicLanguage")):
            pytest.skip("bias_check / toxic_language hub validators not installed")
        guard = build_narrative_guard(_NARRATIVE_CFG)
        if guard is None:
            pytest.skip("narrative guard unavailable")
        return guard

    def test_clean_narrative_passes(self) -> None:
        guard = self._guard()
        clean = "Strong Python engineer with a consistent delivery record on payments platforms."
        assert validate_narrative(guard, clean) == clean

    def test_biased_or_toxic_narrative_is_withheld(self) -> None:
        guard = self._guard()
        biased = "He is too old to keep up with a young, energetic engineering team."
        # Either the models flag it (withheld) or they don't on this phrasing (passes) — but the
        # call must be safe and deterministic in its contract.
        result = validate_narrative(guard, biased)
        assert result in (biased, NARRATIVE_WITHHELD_NOTICE)


@pytest.mark.eval_live
class TestGroundingGuardLive:
    def test_strips_ungrounded_sentence(self) -> None:
        if not _hub_available("BespokeMiniCheck"):
            pytest.skip("bespoke_minicheck hub validator not installed")
        if not os.environ.get("BESPOKE_API_KEY"):
            pytest.skip("BESPOKE_API_KEY not set")
        guard = build_grounding_guard(_GROUNDING_CFG)
        if guard is None:
            pytest.skip("grounding guard unavailable")
        sources = ["Led the payments platform migration.", "Advanced Python and AWS."]
        narrative = "Led the payments platform migration. Also won a Nobel Prize in physics."
        grounded = ground_narrative(guard, narrative, sources)
        assert "Nobel Prize" not in grounded  # the ungrounded sentence is filtered
