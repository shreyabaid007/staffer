"""Pure builders for the capability-only index text (a-005; AD-072/AD-081/AD-084).

``build_embed_text`` composes the PII-free passage that gets embedded; ``build_skill_set`` (T-003)
builds the exact hard-skill / BM25 input. Both consume the **same** included-skills predicate so
the dense and structured views never disagree, and both read **only** capability fields of gold
(``skills``/``domains``/``projects``) — never identity, by construction (AD-084).

This module is the single seam where Lane C's later generic outbound NER/org-dictionary scan would
attach (AD-084); no per-candidate known-PII scan runs here.
"""

from __future__ import annotations

from dsm.ingest.models import GoldCandidate, MergedSkill


def included_skills(gold: GoldCandidate) -> list[MergedSkill]:
    """Skills that may enter the index: ``demonstrated`` True/None kept, False excluded.

    A feedback-refuted skill (``demonstrated is False``, MG-5) is dropped so a later exact /
    ``ARRAY_CONTAINS`` / BM25 hard-skill match can never credit a refuted skill (AD-081), and the
    embedded passage stays negation-free (embeddings cannot represent negation, AD-072). One
    predicate drives both builders, so ``embed_text`` and ``skill_set`` always agree.
    """
    return [s for s in gold.skills if s.demonstrated is not False]


def build_embed_text(gold: GoldCandidate) -> str:
    """Build the deterministic, PII-free capability passage for embedding (IDX-2; AD-072/AD-084).

    Composition (all parts sorted, so identical gold → byte-identical text and the input skill
    order does not matter):

    1. a one-line contextual prefix ``"Domains: a, b."`` (omitted when there are no domains);
    2. skill phrases ``"<name> <proficiency>"`` (or just ``<name>`` when proficiency is absent),
       sorted by name and joined with ``", "``;
    3. project descriptions (joined with spaces) — these carry the seniority *evidence* (led
       delivery, scale, years), which is why grade-as-a-label is never embedded (AD-072).

    Reads only ``gold.domains``/``gold.skills``/``gold.projects`` — never identity or vault refs,
    so no PII can enter the passage (AD-084); the guarantee is by construction, asserted by test.
    """
    parts: list[str] = []

    domains = sorted(d.value for d in gold.domains)
    if domains:
        parts.append(f"Domains: {', '.join(domains)}.")

    skills = sorted(included_skills(gold), key=lambda s: s.name)
    if skills:
        phrases = [
            f"{s.name} {s.proficiency.value}" if s.proficiency is not None else s.name
            for s in skills
        ]
        parts.append(", ".join(phrases) + ".")

    projects = sorted(gold.projects)
    if projects:
        parts.append(" ".join(projects))

    return " ".join(parts)
