"""Index-layer data contracts (a-005; ee-query/ingestion-architecture).

The **searchable, PII-free** Milvus row (``CandidateIndexRecord``) + the query-time retrieval
provenance model (``RetrievedCandidate``) + the per-run ``IndexMetrics``. This module is
**ingest-free** (AD-091): ``Grade`` comes from the shared ``dsm.models`` home, and the
gold→record projection helpers (``is_indexable``/``project_filter_fields``/``build_record``, which
need ``GoldCandidate``) live in the ``dsm/index/build.py`` build edge. So the whole query-time read
path (``models`` → ``retrieve``/``milvus_store``) imports no ``dsm.ingest``.
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
    embed_text: str  # capability-only, PII-free — the embedded passage (stored for audit)
    dense_vector: list[float]  # 768-dim, L2-normalized (from EmbedClient.embed)
    skill_set: list[str]  # EXCLUDES demonstrated-False skills (AD-081)
    grade: Grade
    city: str | None  # None for Remote (India) — no base city (AD-075)
    remote_within_country: bool  # AD-086 (replaces remote_eligible)
    onsite_cities: list[str]  # AD-086; sorted list (Milvus has no set type)
    availability_type: AvailabilityType
    availability_date: date | None  # None for free_now
    valid_as_of: date | None
    gold_hash: str  # change-detection (AD-082)
    model_version: str  # embedder id; re-embed on change (AD-082)


class FilterFields(TypedDict):
    """The structured projection of a gold entity's supply fields (precise types per field)."""

    grade: Grade
    city: str | None
    remote_within_country: bool
    onsite_cities: list[str]
    availability_type: AvailabilityType
    availability_date: date | None
    valid_as_of: date | None
    gold_hash: str


class RetrievedCandidate(BaseModel, frozen=True):
    """A candidate surviving the exact filter, carrying recall/rerank provenance (§5; AD-089).

    All scores are optional: with hybrid recall deferred (``index.recall.enabled = false``, AD-089)
    ``dense_score``/``bm25_score``/``rrf_score`` are ``None``; on a rerank ``EmbedError`` the
    fallback passes the pool through unranked, so ``rerank_score`` is ``None`` (§6.7). It is
    provenance for ``explain`` — **not** the final sort key (step 9 rank decides order).
    """

    candidate_id: str  # HMAC(email), AD-067 — store key + join back to the hydrated Candidate
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
