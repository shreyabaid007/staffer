"""Deterministic PII redaction for the outbound LLM boundary (AD-069; placement AD-078).

Order (AD-069, defense in depth):
1. **Deterministically** remove *known* identifiers (the name + email handed in by the caller,
   e.g. from the supply row) — exact, case-insensitive. This is the load-bearing guarantee.
2. Run NER over the residual for **unknown** residual names + client-org names and tokenize those
   too. NER is a mockable seam (``ner=`` / module ``_default_ner``); Lane C hardens it later
   (Indian-surname tuning, a client-org dictionary — ee-ingestion §15#4). The deterministic pass +
   the outbound leak-scan (``dsm.pii.leakscan``) backstop any NER imperfection.

The public surface is **generic** — plain ``text`` + ``known_pii`` strings, no ingest assumptions —
so every LLM call site (ingest enrich now; match/clarify, match/score later) reuses it without an
``dsm.ingest`` import. The placeholder→original ``mapping`` is returned for de-anonymisation and is
**in-memory only**: this module never persists or logs it (tech.md "never log the pseudonym map").
"""

from __future__ import annotations

import re
from collections.abc import Callable

from pydantic import BaseModel, Field

# A NER span: (surface_text, entity_type). The seam returns these for the residual pass.
NerSpan = tuple[str, str]
NerFn = Callable[[str], list[NerSpan]]

_KNOWN_PREFIX = "PII"
_NER_PREFIX = "NER"
# Entities the NER residual pass tokenizes. Location is deliberately NOT here — it is a kept match
# signal (§9). Names/orgs/email/phone are PII.
_NER_ENTITIES = ("PERSON", "ORG", "EMAIL_ADDRESS", "PHONE_NUMBER")


class RedactionResult(BaseModel):
    """Redacted text plus the in-memory placeholder→original map used to de-anonymise output."""

    text: str
    mapping: dict[str, str] = Field(default_factory=dict)


def _placeholder(prefix: str, index: int) -> str:
    return f"[[{prefix}_{index}]]"


def _dedupe_known(known_pii: list[str]) -> list[str]:
    """Trim, drop blanks, case-insensitively dedupe, then sort **longest-first**.

    Longest-first so a full identifier (``"Aarav Sharma"``) is replaced before a substring of it
    (``"Aarav"``) — otherwise the substring pass would leave a dangling placeholder fragment.
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in known_pii:
        s = raw.strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    out.sort(key=lambda x: (-len(x), x.lower()))
    return out


def redact(
    text: str,
    *,
    known_pii: list[str],
    ner: NerFn | None = None,
) -> RedactionResult:
    """Redact known identifiers then NER residuals; return redacted text + de-anon mapping.

    Deterministic for a fixed ``(text, known_pii, ner)``: placeholders are assigned in a stable
    (longest-first, then lexical) order, so the same input yields byte-identical output.
    """
    mapping: dict[str, str] = {}
    redacted = text

    # 1. Deterministic known-PII pass (case-insensitive exact match).
    for i, identifier in enumerate(_dedupe_known(known_pii)):
        pattern = re.compile(re.escape(identifier), re.IGNORECASE)
        if pattern.search(redacted):
            placeholder = _placeholder(_KNOWN_PREFIX, i)
            redacted = pattern.sub(placeholder, redacted)
            mapping[placeholder] = identifier

    # 2. NER residual pass (mockable seam).
    ner_fn = ner or _default_ner
    already = {v.lower() for v in mapping.values()}
    residual: list[str] = []
    seen: set[str] = set()
    for span_text, _entity_type in ner_fn(redacted):
        s = span_text.strip()
        if s and s.lower() not in seen and s.lower() not in already:
            seen.add(s.lower())
            residual.append(s)
    residual.sort(key=lambda x: (-len(x), x.lower()))
    for j, span in enumerate(residual):
        if span in redacted:
            placeholder = _placeholder(_NER_PREFIX, j)
            redacted = redacted.replace(span, placeholder)
            mapping[placeholder] = span

    return RedactionResult(text=redacted, mapping=mapping)


def deanonymize(text: str, mapping: dict[str, str]) -> str:
    """Restore originals into structured output by replacing each placeholder token."""
    restored = text
    for placeholder, original in mapping.items():
        restored = restored.replace(placeholder, original)
    return restored


_analyzer: object | None = None


def _default_ner(text: str) -> list[NerSpan]:
    """Default NER seam (Presidio). Degrades to ``[]`` when the model is unavailable.

    Guarded + lazy: a missing spaCy model must not crash the pipeline or block offline tests — the
    deterministic known-PII pass + the outbound leak-scan remain the hard guarantees (AD-069). Lane
    C hardens this (model provisioning, surname/org tuning). Unit tests inject a fake ``ner``.
    """
    global _analyzer
    try:
        if _analyzer is None:
            from presidio_analyzer import AnalyzerEngine

            _analyzer = AnalyzerEngine()
        results = _analyzer.analyze(  # type: ignore[attr-defined]
            text=text, entities=list(_NER_ENTITIES), language="en"
        )
        return [(text[r.start : r.end], r.entity_type) for r in results]
    except Exception:
        return []
