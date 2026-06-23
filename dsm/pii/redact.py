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
        if not isinstance(raw, str):  # defensive: a non-str entry must not crash a redaction
            continue
        s = raw.strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    out.sort(key=lambda x: (-len(x), x.lower()))
    return out


class Redactor:
    """A redaction session that holds **one** placeholder→original mapping across many fragments.

    A single LLM call spans several text fragments (the ``prompt`` plus each chat ``message``
    content). Redacting each fragment with an independent :func:`redact` would assign clashing
    ``NER_j`` indices (fragment 2's ``NER_0`` ≠ fragment 1's), so a unified response
    de-anonymisation would be ambiguous. ``Redactor`` shares the mapping so the **same surface form
    yields the same placeholder everywhere in the call** (AD-101 / R-09):

    - **Known-PII placeholders are index-stable by construction:** ``PII_i`` is keyed to position
      ``i`` in the longest-first-sorted ``known_pii`` list, independent of which fragment it is in.
    - **NER residual placeholders are made stable** via a cumulative ``surface→placeholder``
      reverse map + a monotonic ``NER`` counter: a span first seen in fragment 1 reuses its
      placeholder when it recurs in fragment 2; new spans get the next index (assigned
      longest-first for determinism).

    Deterministic for a fixed ``(fragments, known_pii, ner)`` processed in a fixed order — the same
    inputs yield byte-identical output. The mapping is **in-memory only** (never persisted/logged).
    """

    def __init__(self, known_pii: list[str], ner: NerFn | None = None) -> None:
        self._known = _dedupe_known(known_pii)  # longest-first, deduped
        self._ner: NerFn = ner or _default_ner
        self._mapping: dict[str, str] = {}  # placeholder → original
        # NER reuse keyed by the **exact** surface (case-sensitive), so a span recurring with its
        # original casing reuses its placeholder AND de-anonymisation restores it verbatim. "John"
        # and "JOHN" get distinct placeholders — both redacted (no leak), each restored exactly.
        self._ner_seen: dict[str, str] = {}  # exact surface → placeholder (cross-fragment reuse)
        self._ner_counter = 0

    @property
    def mapping(self) -> dict[str, str]:
        """Cumulative placeholder→original map (used by :func:`deanonymize` on the response)."""
        return self._mapping

    def redact(self, text: str) -> str:
        """Redact one fragment, updating the shared mapping; returns the redacted text."""
        return self._apply_ner(self._apply_known(text))

    def _apply_known(self, text: str) -> str:
        """Deterministic known-PII pass (case-insensitive); index-stable ``PII_i`` placeholders.

        **Single pass** over the original text via one longest-first alternation, so a short
        identifier can never match *inside* a placeholder this pass already inserted (e.g.
        ``known=["Aarav Pii", "Ii"]`` must not let ``"Ii"`` corrupt ``"[[PII_0]]"``). Regex
        alternation is leftmost-first, and ``self._known`` is pre-sorted longest-first, so the
        longest identifier wins at any position — preserving the substring guarantee.
        """
        if not self._known:
            return text
        index = {identifier.lower(): i for i, identifier in enumerate(self._known)}
        pattern = re.compile(
            "|".join(re.escape(identifier) for identifier in self._known), re.IGNORECASE
        )

        def _replace(match: re.Match[str]) -> str:
            i = index[match.group(0).lower()]
            placeholder = _placeholder(_KNOWN_PREFIX, i)
            self._mapping[placeholder] = self._known[i]
            return placeholder

        return pattern.sub(_replace, text)

    def _apply_ner(self, text: str) -> str:
        """NER residual pass (mockable seam); reuses placeholders for spans seen in prior frags.

        Runs on the **known-redacted** text, so it never re-sees a known identifier. Dedup +
        reuse are case-sensitive (exact surface) for verbatim round-trips.
        """
        surfaces: list[str] = []
        seen: set[str] = set()
        for span_text, _entity_type in self._ner(text):
            s = span_text.strip()
            if s and s not in seen:
                seen.add(s)
                surfaces.append(s)
        # Assign placeholders to genuinely new surfaces (longest-first → stable indices).
        new_surfaces = sorted(
            (s for s in surfaces if s not in self._ner_seen),
            key=lambda x: (-len(x), x),
        )
        for span in new_surfaces:
            placeholder = _placeholder(_NER_PREFIX, self._ner_counter)
            self._ner_counter += 1
            self._mapping[placeholder] = span
            self._ner_seen[span] = placeholder
        # Apply replacements (longest-first) for every detected surface present in this fragment.
        redacted = text
        for span in sorted(surfaces, key=lambda x: (-len(x), x)):
            if span in redacted:
                redacted = redacted.replace(span, self._ner_seen[span])
        return redacted


def redact(
    text: str,
    *,
    known_pii: list[str],
    ner: NerFn | None = None,
) -> RedactionResult:
    """Redact known identifiers then NER residuals; return redacted text + de-anon mapping.

    Single-fragment convenience over :class:`Redactor` (one code path). Deterministic for a fixed
    ``(text, known_pii, ner)``: placeholders are assigned in a stable (longest-first, then lexical)
    order, so the same input yields byte-identical output.
    """
    redactor = Redactor(known_pii, ner=ner)
    redacted = redactor.redact(text)
    return RedactionResult(text=redacted, mapping=redactor.mapping)


def redact_fragments(
    texts: list[str],
    *,
    known_pii: list[str],
    ner: NerFn | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Redact a batch of fragments under **one** :class:`Redactor`; return ``(redacted, mapping)``.

    Used by ``PseudonymisedLM`` to redact a ``prompt`` + several ``messages`` under one coherent
    mapping, so the response de-anonymisation is unambiguous (R-09). Order is preserved.
    """
    redactor = Redactor(known_pii, ner=ner)
    redacted = [redactor.redact(t) for t in texts]
    return redacted, redactor.mapping


def deanonymize(text: str, mapping: dict[str, str]) -> str:
    """Restore originals into structured output by replacing each placeholder token.

    Iterates to a fixpoint (bounded by the mapping size) so that a mapped value which itself
    contains a placeholder — e.g. an NER span that straddled an already-redacted known identifier,
    ``mapping = {"[[NER_0]]": "[[PII_0]] Smith", "[[PII_0]]": "Aarav"}`` — is fully resolved
    regardless of insertion order. Placeholders never self-reference, so the loop always converges.
    """
    restored = text
    for _ in range(len(mapping) + 1):
        before = restored
        for placeholder, original in mapping.items():
            restored = restored.replace(placeholder, original)
        if restored == before:
            break
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
