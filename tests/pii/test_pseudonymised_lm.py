"""Tests for the real PseudonymisedLM boundary (c-003 T-002; AD-097). R-01/02/03/04/12.

No live network: the wrapped provider call (``dspy.LM.__call__``) is monkeypatched to a fake that
captures what it received and returns a canned response; NER is an injected fake. The boundary
logic (redact → leak-scan → forward → de-anonymise) is exercised in full offline.
"""

from __future__ import annotations

from typing import Any

import dspy
import pytest

from dsm.pii.leakscan import PIILeakError
from dsm.pii.pseudonymised_lm import PseudonymisedLM, pii_context
from dsm.pii.redact import NerSpan

_MODEL = "openrouter/anthropic/claude-sonnet-4-6"


def _capture_base(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch the wrapped provider call to echo back the (redacted) content it was sent."""
    captured: dict[str, Any] = {}

    def fake_base_call(
        self: dspy.LM,
        prompt: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> list[Any]:
        captured["prompt"] = prompt
        captured["messages"] = messages
        captured["called"] = True
        # Echo the content the provider "saw" so the test can assert de-anonymisation restores it.
        if messages:
            return [messages[-1]["content"]]
        return [prompt or ""]

    captured["called"] = False
    monkeypatch.setattr(dspy.LM, "__call__", fake_base_call)
    return captured


def test_redacts_known_pii_before_forwarding_and_restores_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R-01: known PII is stripped before the provider sees it; the response is de-anonymised."""
    captured = _capture_base(monkeypatch)
    lm = PseudonymisedLM(model=_MODEL, ner=lambda _t: [])

    with pii_context(["Aarav Sharma"]):
        out = lm(messages=[{"role": "user", "content": "Assess Aarav Sharma for the role."}])

    sent = captured["messages"][-1]["content"]
    assert "Aarav Sharma" not in sent  # provider never saw the name
    assert "[[PII_0]]" in sent
    # The echoed (redacted) content is restored on the way back out.
    assert out == ["Assess Aarav Sharma for the role."]


def test_leak_scan_blocks_call_when_redaction_misses(monkeypatch: pytest.MonkeyPatch) -> None:
    """R-02: a known-PII string surviving redaction raises PIILeakError; provider never called."""
    captured = _capture_base(monkeypatch)

    # Simulate a redaction miss: a Redactor that returns text unchanged with an empty mapping.
    class _LeakyRedactor:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            self.mapping: dict[str, str] = {}

        def redact(self, text: str) -> str:
            return text

    monkeypatch.setattr("dsm.pii.pseudonymised_lm.Redactor", _LeakyRedactor)
    lm = PseudonymisedLM(model=_MODEL, ner=lambda _t: [])

    with pii_context(["Aarav Sharma"]), pytest.raises(PIILeakError):
        lm(messages=[{"role": "user", "content": "Assess Aarav Sharma."}])
    assert captured["called"] is False  # blocked before forwarding


def test_unset_context_is_pass_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """R-03: with no pii_context active, the call is forwarded unchanged (clarify path)."""
    captured = _capture_base(monkeypatch)
    lm = PseudonymisedLM(model=_MODEL, ner=lambda _t: [])

    messages = [{"role": "user", "content": "Refine the scorecard for a payments role."}]
    out = lm(messages=messages)

    assert captured["messages"] == messages  # byte-identical, no redaction
    assert out == ["Refine the scorecard for a payments role."]


def test_empty_context_still_engages_ner(monkeypatch: pytest.MonkeyPatch) -> None:
    """R-03: a context set to [] is distinct from unset — the NER residual pass still runs."""
    captured = _capture_base(monkeypatch)

    def fake_ner(text: str) -> list[NerSpan]:
        return [("Priya Nair", "PERSON")] if "Priya Nair" in text else []

    lm = PseudonymisedLM(model=_MODEL, ner=fake_ner)
    with pii_context([]):
        lm(messages=[{"role": "user", "content": "Reviewed by Priya Nair."}])

    assert "Priya Nair" not in captured["messages"][-1]["content"]
    assert "[[NER_0]]" in captured["messages"][-1]["content"]


def test_prompt_and_messages_share_one_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    """R-09/R-01: prompt + messages redacted under one mapping (same name → same placeholder)."""
    captured = _capture_base(monkeypatch)
    lm = PseudonymisedLM(model=_MODEL, ner=lambda _t: [])

    with pii_context(["Aarav Sharma"]):
        lm(
            prompt="Context about Aarav Sharma.",
            messages=[{"role": "user", "content": "Now assess Aarav Sharma."}],
        )

    assert "Aarav Sharma" not in captured["prompt"]
    assert "Aarav Sharma" not in captured["messages"][-1]["content"]
    # Same identifier → same PII_0 placeholder in both fragments.
    assert "[[PII_0]]" in captured["prompt"]
    assert "[[PII_0]]" in captured["messages"][-1]["content"]


def test_context_is_restored_after_block_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """R-04: the context var is reset on exit, so a later call defaults back to pass-through."""
    captured = _capture_base(monkeypatch)
    lm = PseudonymisedLM(model=_MODEL, ner=lambda _t: [])

    with pii_context(["Aarav Sharma"]):
        lm(messages=[{"role": "user", "content": "Assess Aarav Sharma."}])
    # Outside the block: unset context → pass-through (name forwarded verbatim).
    lm(messages=[{"role": "user", "content": "Plain Aarav Sharma."}])
    assert captured["messages"][-1]["content"] == "Plain Aarav Sharma."
