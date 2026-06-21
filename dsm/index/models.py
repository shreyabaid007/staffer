"""Index-layer contracts (a-005, ee-ingestion-architecture §6 Phase 6; AD-091 refactor).

Data models for the searchable, PII-free ``CandidateIndexRecord`` written to Milvus Lite.
Filter fields come from the ``Sourced[...].value`` of the gold supply fields; ``gold_hash`` +
``model_version`` (the *embedder* id) gate re-embedding (AD-082).

AD-091: ``Grade`` is now imported from ``dsm.models`` (shared). The gold-to-record projection
helpers (``is_indexable``, ``project_filter_fields``, ``build_record``) and write-time text
builders (``build_embed_text``, ``build_skill_set``, ``included_skills``) live in
``dsm/index/build.py`` (the build edge, exempt from the ``index ⊥ ingest`` import contract).
"""

from __future__ import annotations

from datetime import date
from typing import Literal, TypedDict

from pydantic import BaseModel

from dsm.models import Grade

AvailabilityType = Literal["free_now", "rolling_off", "new_joiner"]


class CandidateIndexRecord(BaseModel, frozen=True):
    """One Milvus row: a capability-only dense vector + structured filter fields (§6 Phase 6).

    PII-free by construction — ``embed_text``/``skill_set`` are built only from capability fields
    of gold (AD-084), and identity is never carried here (no name/email/vault ref).
    ``model_version`` is the embedder id (= ``config models.embedder``), distinct from
    ``GoldCandidate.model_version`` (reasoning LLM at enrich); the index re-embeds on it (AD-082).
    """

    candidate_id: str
    embed_text: str
    dense_vector: list[float]
    skill_set: list[str]
    grade: Grade
    city: str | None
    remote_within_country: bool
    onsite_cities: list[str]
    availability_type: AvailabilityType
    availability_date: date | None
    valid_as_of: date | None
    gold_hash: str
    model_version: str


class FilterFields(TypedDict):
    """The structured projection of a gold entity's supply fields."""

    grade: Grade
    city: str | None
    remote_within_country: bool
    onsite_cities: list[str]
    availability_type: AvailabilityType
    availability_date: date | None
    valid_as_of: date | None
    gold_hash: str


class RetrievedCandidate(BaseModel, frozen=True):
    """A candidate surviving recall/rerank, carrying provenance scores (AD-089)."""

    candidate_id: str
    dense_score: float | None = None
    bm25_score: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None


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
