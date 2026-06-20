"""Index-layer contracts (a-005, ee-ingestion-architecture §6 Phase 6).

Projects a canonical ``GoldCandidate`` into the searchable, PII-free ``CandidateIndexRecord``
written to Milvus Lite. Filter fields come from the ``Sourced[...].value`` of the gold supply
fields; ``gold_hash`` + ``model_version`` (the *embedder* id) gate re-embedding (AD-082).

``Grade`` is imported from ``dsm.ingest.models`` (never redefined); ``Location``/availability
variants come from the frozen ``dsm.models``. ``dsm.index`` may read ``dsm.ingest`` (the reverse
of the ``ingest ⊥ index`` contract, NF-2).
"""

from __future__ import annotations

from datetime import date
from typing import Literal, TypedDict

from pydantic import BaseModel

from dsm.ingest.models import GoldCandidate, Grade
from dsm.models import NewJoiner, RollingOff

AvailabilityType = Literal["free_now", "rolling_off", "new_joiner"]


class CandidateIndexRecord(BaseModel, frozen=True):
    """One Milvus row: a capability-only dense vector + structured filter fields (§6 Phase 6).

    PII-free by construction — ``embed_text``/``skill_set`` are built only from capability fields
    of gold (AD-084), and identity is never carried here (no name/email/vault ref).
    ``model_version`` is the embedder id (= ``config models.embedder``), distinct from
    ``GoldCandidate.model_version`` (reasoning LLM at enrich); the index re-embeds on it (AD-082).
    """

    candidate_id: str
    embed_text: str  # capability-only, PII-free — the embedded passage (stored for audit)
    dense_vector: list[float]  # 768-dim, L2-normalized (from EmbedClient.embed)
    skill_set: list[str]  # EXCLUDES demonstrated-False skills (AD-081)
    grade: Grade
    city: str | None  # None for Remote (India) — no base city (AD-075)
    remote_eligible: bool
    availability_type: AvailabilityType
    availability_date: date | None  # None for free_now
    valid_as_of: date | None
    gold_hash: str  # change-detection (AD-082)
    model_version: str  # embedder id; re-embed on change (AD-082)


class FilterFields(TypedDict):
    """The structured projection of a gold entity's supply fields (precise types per field)."""

    grade: Grade
    city: str | None
    remote_eligible: bool
    availability_type: AvailabilityType
    availability_date: date | None
    valid_as_of: date | None
    gold_hash: str


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
        remote_eligible=loc.remote_eligible,
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
        remote_eligible=fields["remote_eligible"],
        availability_type=fields["availability_type"],
        availability_date=fields["availability_date"],
        valid_as_of=fields["valid_as_of"],
        gold_hash=fields["gold_hash"],
        model_version=model_version,
    )


class IndexMetrics(BaseModel):
    """Per-run index outcome counts (mirrors ``dsm.ingest.lineage.QualityMetrics``).

    There is no PII-leak failure mode at index time — ``embed_text`` is PII-free by construction
    (AD-084), so unlike the ingest metrics this carries no ``leak_blocks`` term. ``assert_clean``
    is a structural sanity guard only.
    """

    indexed: int = 0
    skipped_unchanged: int = 0
    tombstoned_removed: int = 0
    thin_skipped: int = 0

    def assert_clean(self) -> None:
        """Sanity invariant: counters are monotonic, never negative (no PII gate here — AD-084)."""
        negatives = {
            name: value
            for name, value in self.model_dump().items()
            if isinstance(value, int) and value < 0
        }
        if negatives:
            raise RuntimeError(f"index metrics went negative — internal bug: {negatives}")
