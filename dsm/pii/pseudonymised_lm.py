"""PseudonymisedLM — the ONLY authorised path to an LLM provider (AD-010, AD-097).

The real outbound PII boundary for **query-time** LLM calls (ingest ``enrich`` runs the same
``redact → leak-scan → de-anonymise`` cycle itself, around its own injected predictor). The wrapper
only ever sees a formatted ``prompt`` / chat ``messages``, so the candidate's *known* identifiers
are supplied out-of-band via :func:`pii_context` — a ``contextvars.ContextVar`` the caller (the CLI
composition root, per AD-097) sets around the call after resolving identity from the vault.

Per call, when a known-PII context is active:

1. **anonymise** every outbound text fragment (``prompt`` + each message ``content``) under one
   :class:`~dsm.pii.redact.Redactor` — deterministic known-identifier strip first (the AD-069
   load-bearing guarantee), then NER residual — so one coherent mapping covers the whole call;
2. **leak-scan (gate)** each redacted fragment with :func:`~dsm.pii.leakscan.assert_no_leak`: any
   surviving known-PII string raises ``PIILeakError`` and **blocks** the provider call (AD-069);
3. **forward** the redacted call to the wrapped provider;
4. **de-anonymise** the response, restoring placeholders the model echoed (e.g. verbatim citation
   quotes) before the response leaves the wrapper.

**Unset context ⇒ pass-through.** ``clarify`` carries role text only — no candidate PII
(``ee-query-architecture.md`` §7) — so it is invoked without :func:`pii_context` and behaves as the
Slice-0 stub did. A context set to ``[]`` is distinct from *unset*: it still engages the NER
residual pass (no known list, but residual names are still tokenised).

The per-call placeholder map is **in-memory only** (AD-010) and never logged.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import dspy

from dsm.pii.leakscan import assert_no_leak
from dsm.pii.redact import NerFn, Redactor, deanonymize

# The active known-PII list for the current call context. ``None`` (the default) means *unset* →
# full pass-through (clarify). A list (possibly empty) engages redaction + leak-scan.
_KNOWN_PII: ContextVar[list[str] | None] = ContextVar("dsm_pii_known", default=None)


@contextmanager
def pii_context(known_pii: list[str]) -> Iterator[None]:
    """Scope a known-PII list to the enclosed LLM call(s); restored on exit (incl. on exception).

    Set by the caller that knows *which* candidate a call concerns (the CLI score-predictor
    wrapper, AD-097), so ``PseudonymisedLM`` can run the deterministic redact-first pass without
    the known list being threaded through ``dsm.match`` as a function argument.

    Non-string entries are dropped defensively — a stray ``None``/int must never crash the
    downstream ``leak_scan`` (which calls ``.strip()``); a non-string is not a redactable id.
    """
    token = _KNOWN_PII.set([p for p in known_pii if isinstance(p, str)])
    try:
        yield
    finally:
        _KNOWN_PII.reset(token)


def _redact_messages(messages: list[dict[str, Any]], redactor: Redactor) -> list[dict[str, Any]]:
    """Redact the textual ``content`` of each chat message under the shared redactor, in order."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        new = dict(msg)
        content = msg.get("content")
        if isinstance(content, str):
            new["content"] = redactor.redact(content)
        elif isinstance(content, list):  # multimodal content parts
            new["content"] = [_redact_part(part, redactor) for part in content]
        out.append(new)
    return out


def _redact_part(part: Any, redactor: Redactor) -> Any:
    """Redact a single multimodal content part (``{"type": "text", "text": ...}``)."""
    if isinstance(part, str):
        return redactor.redact(part)
    if isinstance(part, dict) and isinstance(part.get("text"), str):
        new = dict(part)
        new["text"] = redactor.redact(part["text"])
        return new
    return part


def _outbound_text(prompt: str | None, messages: list[dict[str, Any]] | None) -> Iterator[str]:
    """Yield every redacted string fragment bound for the provider (for the leak-scan gate)."""
    if isinstance(prompt, str):
        yield prompt
    for msg in messages or []:
        content = msg.get("content")
        if isinstance(content, str):
            yield content
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, str):
                    yield part
                elif isinstance(part, dict) and isinstance(part.get("text"), str):
                    yield part["text"]


def _deanonymize_any(item: Any, mapping: dict[str, str]) -> Any:
    """Recursively restore placeholders in a response element (str / dict / list)."""
    if isinstance(item, str):
        return deanonymize(item, mapping)
    if isinstance(item, dict):
        return {k: _deanonymize_any(v, mapping) for k, v in item.items()}
    if isinstance(item, list):
        return [_deanonymize_any(v, mapping) for v in item]
    return item


class PseudonymisedLM(dspy.LM):
    """Wraps a DSPy LM, stripping known + residual PII before the call and restoring it after.

    Every module that needs an LLM MUST obtain it from here, never from ``dspy.LM`` directly
    (AD-010; enforced by the ``No direct LLM provider access`` import contract).
    """

    def __init__(self, model: str, *, ner: NerFn | None = None, **kwargs: object) -> None:
        super().__init__(model=model, **kwargs)  # type: ignore[arg-type]
        # NER seam for the residual pass; ``None`` → Redactor's default (guarded Presidio). Tests
        # inject a fake to stay offline + deterministic.
        self._ner = ner

    def __call__(  # type: ignore[override]
        self,
        prompt: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        **kwargs: object,
    ) -> list[Any]:
        known = _KNOWN_PII.get()
        if known is None:  # unset context → pass-through (clarify, §7)
            return super().__call__(prompt, messages=messages, **kwargs)  # type: ignore[arg-type,no-any-return]

        redactor = Redactor(known, ner=self._ner)  # one mapping for the whole call (R-09)
        anon_prompt = redactor.redact(prompt) if isinstance(prompt, str) else prompt
        anon_messages = _redact_messages(messages, redactor) if messages else messages

        for fragment in _outbound_text(anon_prompt, anon_messages):
            assert_no_leak(fragment, known_pii=known)  # hard gate before the provider sees it

        out = super().__call__(anon_prompt, messages=anon_messages, **kwargs)  # type: ignore[arg-type]
        return [_deanonymize_any(item, redactor.mapping) for item in out]
