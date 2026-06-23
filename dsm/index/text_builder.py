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
from dsm.models import ProficiencyLevel, TargetProfileScorecard


def _skill_phrase(name: str, proficiency: ProficiencyLevel | None) -> str:
    """The shared skill-span format for both the candidate passage and the role query passage.

    A single helper so the dense candidate passage (``build_embed_text``) and the dense role query
    passage (``build_role_query_passage``) render a skill the **same** way — ``"<name> <prof>"``
    when a proficiency/floor is present, else just ``<name>``. Symmetry matters because the BGE
    model compares the asymmetric query/passage pair; a divergent span would degrade recall/rerank.
    """
    return f"{name} {proficiency.value}" if proficiency is not None else name


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
        phrases = [_skill_phrase(s.name, s.proficiency) for s in skills]
        parts.append(", ".join(phrases) + ".")

    projects = sorted(gold.projects)
    if projects:
        parts.append(" ".join(projects))

    return " ".join(parts)


def build_skill_set(gold: GoldCandidate) -> list[str]:
    """Build the exact hard-skill / BM25 skill list — deduped, sorted (IDX-3; AD-081).

    ``sorted({s.name for s in included_skills(gold)})``: a feedback-refuted skill
    (``demonstrated is False``) is excluded by the shared predicate, so an exact / ARRAY_CONTAINS /
    BM25 hard-skill match can never credit a refuted skill; confirmed and unverified skills stay.
    Sorted + deduped for determinism (drives both the ``skill_set`` ARRAY and ``skill_text``).
    """
    return sorted({s.name for s in included_skills(gold)})


def build_role_query_passage(scorecard: TargetProfileScorecard) -> str:
    """Build the role-side query passage, symmetric to ``build_embed_text`` (b-002; §6.6/§6.7).

    Capability-only and deterministic (sorted), composed from the fields the scorecard already
    carries — **no** ``TargetProfileScorecard`` amendment (§12 #7):

    1. **skill phrases** for the hard + desired requirements, rendered via the shared
       ``_skill_phrase`` so a hard skill carries its ``min_proficiency`` floor inline (the
       "min_proficiency-derived seniority" signal) exactly as the candidate passage carries a
       skill's proficiency; sorted by name, joined with ``", "``;
    2. the role's ``clarification_notes`` (the role-side free-text capability context, the analog
       of the candidate passage's project descriptions), appended verbatim when present.

    The BGE **query** instruction prefix is **not** baked in here — it is applied at embed time via
    ``EmbedClient.embed(mode="query")`` (asymmetric passage/query, AD-072), mirroring how the
    candidate passage is embedded with ``mode="passage"``.
    """
    parts: list[str] = []

    requirements = sorted(
        [*scorecard.hard_depth_skills, *scorecard.desired_skills], key=lambda r: r.name
    )
    if requirements:
        phrases = [_skill_phrase(r.name, r.min_proficiency) for r in requirements]
        parts.append(", ".join(phrases) + ".")

    if scorecard.clarification_notes:
        notes = scorecard.clarification_notes.strip()
        if notes:
            parts.append(notes)

    return " ".join(parts)
