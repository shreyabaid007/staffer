"""Index BUILD edge — gold → ``CandidateIndexRecord`` projection (a-005; relocated by AD-091).

These helpers project a canonical ``GoldCandidate`` into the searchable, PII-free
``CandidateIndexRecord`` written to Milvus at ``dsm index`` write-time. They were moved here from
``dsm/index/models.py`` (AD-091) because they are the *only* reason the index data contract needed
``dsm/ingest`` — relocating them lets ``dsm/index/models.py`` (and the whole query-time read
path) stay **ingest-free**, while this module remains a **build / composition edge** that imports
``dsm.ingest`` (exempt from the ``match``/``index`` ⊥ ``ingest`` import contract, like the CLI).

Run only by ``dsm/index/indexer.py`` and the ``dsm index`` CLI — never by the query pipeline.
"""

from __future__ import annotations

from dsm.index.models import CandidateIndexRecord, FilterFields
from dsm.ingest.models import GoldCandidate
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
    assert gold.grade is not None, "project_filter_fields requires an indexable gold (grade)"
    assert gold.location is not None, "project_filter_fields requires an indexable gold (location)"
    assert gold.availability is not None, "project_filter_fields requires indexable gold (avail)"

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
