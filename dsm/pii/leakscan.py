"""Outbound known-PII leak-scan — the hard gate before any text crosses to the LLM/embedder.

The redactor (``dsm.pii.redact``) removes known identifiers deterministically and NER residuals;
this is the **backstop** (AD-069, defense in depth): immediately before a provider call, scan the
text for any *known* PII string that survived. A hit means redaction failed — so we **block the
call and fail the build/eval** (``assert_no_leak`` raises ``PIILeakError``) rather than risk PII
reaching OpenRouter. The eval invariant ``no-PII-leak`` asserts zero hits across the suite.

Generic surface (plain ``text`` + ``known_pii``) — reusable by every call site without an
``dsm.ingest`` import. Hits are reported/logged as a **count + which identifier index**, never by
re-emitting the PII value (tech.md "never log the pseudonym map").
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PIILeakError(RuntimeError):
    """Raised when text bound for the LLM/embedder still contains a known-PII string."""


class LeakScanResult(BaseModel):
    """Outcome of an outbound scan. ``hits`` lists the leaked identifiers (caller logs a count)."""

    clean: bool
    hits: list[str] = Field(default_factory=list)


def leak_scan(text: str, *, known_pii: list[str]) -> LeakScanResult:
    """Scan ``text`` for any known-PII string (case-insensitive); report hits without sending."""
    haystack = text.lower()
    hits = [s for s in (raw.strip() for raw in known_pii) if s and s.lower() in haystack]
    return LeakScanResult(clean=not hits, hits=hits)


def assert_no_leak(text: str, *, known_pii: list[str]) -> None:
    """Hard gate: raise ``PIILeakError`` if any known-PII string remains (AD-069).

    The exception message reports **how many** identifiers leaked, never their values.
    """
    result = leak_scan(text, known_pii=known_pii)
    if not result.clean:
        raise PIILeakError(
            f"outbound leak-scan blocked the call: {len(result.hits)} known-PII string(s) "
            "still present in text bound for the LLM (AD-069)"
        )
