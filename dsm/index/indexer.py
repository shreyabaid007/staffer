"""Gold → embed → upsert orchestration (a-005; IDX-4/IDX-6/IDX-7/IDX-8).

For each ``candidate_id``: read gold, then route — tombstone → delete; thin (missing a required
filter field) → skip + log; ``(gold_hash, model_version)`` unchanged → skip (no re-embed, AD-082);
otherwise build the PII-free passage, embed it (passage mode), and upsert the record. Returns the
run's ``IndexMetrics``.

Depends on the injected ``EmbedClient`` **protocol** (production injects ``ModalEmbedClient``,
tests a ``FakeEmbedClient``) and an injected ``MilvusIndexStore``. No ``dsm.pii`` import this
slice — ``embed_text`` is PII-free by construction, so there is no leak/exit path (AD-084).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

import structlog

from dsm.index.build import (
    build_embed_text,
    build_record,
    build_skill_set,
    is_indexable,
)
from dsm.index.embed_client import EmbedClient
from dsm.index.milvus_store import MilvusIndexStore
from dsm.index.models import IndexMetrics
from dsm.ingest.models import GoldCandidate

_log = structlog.get_logger("dsm.index")


def _missing_fields(gold: GoldCandidate) -> list[str]:
    """The required filter fields a thin gold entity is missing (for a PII-safe skip log)."""
    return [
        name
        for name, present in (
            ("grade", gold.grade is not None),
            ("location", gold.location is not None),
            ("availability", gold.availability is not None),
        )
        if not present
    ]


def index_gold(
    candidate_ids: Iterable[str],
    *,
    read_gold: Callable[[str], GoldCandidate | None],
    store: MilvusIndexStore,
    embed_client: EmbedClient,
    model_version: str,
    run_id: str = "",
) -> IndexMetrics:
    """Index each gold candidate into the store, re-embedding only what changed (AD-082).

    Args:
        candidate_ids: the gold ids to process this run.
        read_gold: reads one ``GoldCandidate`` by id, or ``None`` if absent.
        store: the Milvus Lite store (already ``ensure_collection``-ed).
        embed_client: the ``EmbedClient`` protocol — passage embeddings come from here.
        model_version: the embedder id (= ``config models.embedder``); half the re-embed gate.
        run_id: opaque run tag for the structured logs (no PII).

    Returns:
        ``IndexMetrics`` — indexed / skipped_unchanged / tombstoned_removed / thin_skipped counts.
    """
    metrics = IndexMetrics()

    for cid in candidate_ids:
        gold = read_gold(cid)
        if gold is None:
            continue  # id with no gold on disk — skip defensively

        if gold.is_tombstoned:  # IDX-7: tombstones delete, never embed/upsert
            store.delete([cid])
            metrics.tombstoned_removed += 1
            _log.info("index.tombstone_removed", run_id=run_id, candidate_id=cid)
            continue

        if not is_indexable(gold):  # IDX-8: missing a required filter field — skip, never guess
            missing = _missing_fields(gold)
            metrics.thin_skipped += 1
            _log.info("index.thin_skip", run_id=run_id, candidate_id=cid, missing=missing)
            continue

        # IDX-6: re-embed only when the stored (gold_hash, model_version) pair does not BOTH match.
        stored = store.fetch_versions([cid]).get(cid)
        if stored == (gold.gold_hash, model_version):
            metrics.skipped_unchanged += 1
            continue

        embed_text = build_embed_text(gold)  # PII-free by construction (AD-084) — no scan
        dense_vector = embed_client.embed([embed_text], mode="passage")[0]
        record = build_record(
            gold,
            embed_text=embed_text,
            dense_vector=dense_vector,
            skill_set=build_skill_set(gold),
            model_version=model_version,
        )
        store.upsert([record])
        metrics.indexed += 1

    return metrics
