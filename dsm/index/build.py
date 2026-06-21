"""Gold-to-index-record projection helpers (build edge — may import dsm.ingest).

This module runs at **write time** (``dsm index``), not at query time. The import contract
``dsm.index ⊥ dsm.ingest`` (AD-091) exempts it because it is the build/composition edge,
analogous to the CLI orchestrator. Query-time code in ``dsm/index/retrieve.py`` and
``dsm/index/text_builder.py`` must NOT import this module.

Contains: ``is_indexable``, ``project_filter_fields``, ``build_record`` (from models.py),
and ``included_skills``, ``build_embed_text``, ``build_skill_set`` (from text_builder.py).
"""

from __future__ import annotations

from dsm.index.models import CandidateIndexRecord, FilterFields
from dsm.ingest.models import GoldCandidate, MergedSkill
from dsm.models import NewJoiner, RollingOff


def is_indexable(gold: GoldCandidate) -> bool:
    """A gold entity is indexable only when all required filter fields are present (IDX-8).

    A ``False`` here is the thin-skip: the record's ``grade``/``availability_type`` are
    non-optional, so we refuse to guess them. Tombstones are handled *before* this check (the
    delete path), so this never sees a tombstoned entity.
    """
    return gold.grade is not None and gold.location is not None and gold.availability is not None


def project_filter_fields(gold: GoldCandidate) -> FilterFields:
    """Project the structured filter fields from a gold entity's ``Sourced[...].value``s (IDX-1).

    Precondition: ``is_indexable(gold)`` — grade/location/availability present. The availability
    discriminated union maps to ``(availability_type, availability_date)``: free_now → no date,
    rolling_off → expected_date, new_joiner → join_date.
    """
    assert gold.grade is not None
    assert gold.location is not None
    assert gold.availability is not None

    loc = gold.location.value
    avail = gold.availability.value
    availability_date = (
        avail.expected_date
        if isinstance(avail, RollingOff)
        else avail.join_date
        if isinstance(avail, NewJoiner)
        else None
    )
    return FilterFields(
        grade=gold.grade.value,
        city=loc.city,
        remote_within_country=loc.remote_within_country,
        onsite_cities=sorted(loc.onsite_cities),
        availability_type=avail.type,
        availability_date=availability_date,
        valid_as_of=gold.valid_as_of,
        gold_hash=gold.gold_hash,
    )


def build_record(
    gold: GoldCandidate,
    *,
    embed_text: str,
    dense_vector: list[float],
    skill_set: list[str],
    model_version: str,
) -> CandidateIndexRecord:
    """Assemble the frozen index record from the gold projection + embedded inputs (IDX-1/4)."""
    fields = project_filter_fields(gold)
    return CandidateIndexRecord(
        candidate_id=gold.candidate_id,
        embed_text=embed_text,
        dense_vector=dense_vector,
        skill_set=skill_set,
        grade=fields["grade"],
        city=fields["city"],
        remote_within_country=fields["remote_within_country"],
        onsite_cities=fields["onsite_cities"],
        availability_type=fields["availability_type"],
        availability_date=fields["availability_date"],
        valid_as_of=fields["valid_as_of"],
        gold_hash=fields["gold_hash"],
        model_version=model_version,
    )


# ---------------------------------------------------------------------------
# Text builders (write-time; PII-free by construction, AD-084)
# ---------------------------------------------------------------------------


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


def build_skill_set(gold: GoldCandidate) -> list[str]:
    """Build the exact hard-skill / BM25 skill list — deduped, sorted (IDX-3; AD-081)."""
    return sorted({s.name for s in included_skills(gold)})
