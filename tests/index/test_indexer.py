"""Tests for dsm.index.indexer.index_gold (a-005 T-005; IDX-4/6/7/8; AC-5).

Fake (no-network) ``EmbedClient`` + tmp Milvus Lite store.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dsm.index.indexer import index_gold
from dsm.index.milvus_store import MilvusIndexStore
from dsm.ingest.models import Confidence, GoldCandidate, Grade, MergedSkill, Sourced
from dsm.models import FreeNow, Location
from tests.index.fakes import FakeEmbedClient

_EMBEDDER = "BAAI/bge-base-en-v1.5"


def _gold(
    cid: str = "cid:abc",
    *,
    gold_hash: str = "sha256:g1",
    grade: Grade | None = Grade.LEAD_CONSULTANT,
    is_tombstoned: bool = False,
) -> GoldCandidate:
    return GoldCandidate(
        candidate_id=cid,
        name_vault_ref=f"name:{cid}",
        email_vault_ref=f"email:{cid}",
        grade=Sourced(value=grade) if grade is not None else None,
        location=Sourced(value=Location(city="Chennai")),
        availability=Sourced(value=FreeNow()),
        skills=[MergedSkill(name="kotlin", confidence=Confidence.MEDIUM)],
        is_tombstoned=is_tombstoned,
        gold_hash=gold_hash,
        merge_version="merge-v1",
        prompt_version="enrich-v1",
        model_version="anthropic/claude-sonnet-4-6",
    )


@pytest.fixture()
def store(tmp_path: Path) -> MilvusIndexStore:
    s = MilvusIndexStore(tmp_path / "milvus.db")
    s.ensure_collection()
    return s


def _reader(golds: dict[str, GoldCandidate]):
    return lambda cid: golds.get(cid)


def test_first_run_indexes_in_passage_mode(store: MilvusIndexStore) -> None:
    golds = {"cid:abc": _gold()}
    fake = FakeEmbedClient()
    metrics = index_gold(
        golds, read_gold=_reader(golds), store=store, embed_client=fake, model_version=_EMBEDDER
    )
    assert metrics.indexed == 1
    assert fake.calls == [
        (["kotlin."], "passage")
    ]  # capability-only passage, passage mode (IDX-4)


def test_identical_rerun_skips_unchanged_with_no_new_embed(store: MilvusIndexStore) -> None:
    """AC-5: an unchanged candidate is not re-embedded on the second run."""
    golds = {"cid:abc": _gold()}
    read_gold = _reader(golds)
    fake = FakeEmbedClient()
    index_gold(golds, read_gold=read_gold, store=store, embed_client=fake, model_version=_EMBEDDER)
    fake.calls.clear()

    metrics = index_gold(
        golds, read_gold=read_gold, store=store, embed_client=fake, model_version=_EMBEDDER
    )
    assert metrics.skipped_unchanged == 1
    assert metrics.indexed == 0
    assert fake.calls == []  # the Fake recorded no new embed


def test_model_version_bump_reembeds(store: MilvusIndexStore) -> None:
    """AC-5: bumping the embedder id re-embeds even though gold_hash is unchanged (AD-082)."""
    golds = {"cid:abc": _gold()}
    read_gold = _reader(golds)
    index_gold(
        golds,
        read_gold=read_gold,
        store=store,
        embed_client=FakeEmbedClient(),
        model_version="emb-1",
    )

    fake = FakeEmbedClient()
    metrics = index_gold(
        golds, read_gold=read_gold, store=store, embed_client=fake, model_version="emb-2"
    )
    assert metrics.indexed == 1
    assert metrics.skipped_unchanged == 0
    assert len(fake.calls) == 1


def test_gold_hash_change_reembeds(store: MilvusIndexStore) -> None:
    golds = {"cid:abc": _gold(gold_hash="sha256:v1")}
    index_gold(
        golds,
        read_gold=_reader(golds),
        store=store,
        embed_client=FakeEmbedClient(),
        model_version=_EMBEDDER,
    )
    changed = {"cid:abc": _gold(gold_hash="sha256:v2")}
    fake = FakeEmbedClient()
    metrics = index_gold(
        changed,
        read_gold=_reader(changed),
        store=store,
        embed_client=fake,
        model_version=_EMBEDDER,
    )
    assert metrics.indexed == 1
    assert len(fake.calls) == 1


def test_tombstone_deletes(store: MilvusIndexStore) -> None:
    """IDX-7: a tombstoned gold is deleted, never embedded/upserted."""
    golds = {"cid:abc": _gold()}
    index_gold(
        golds,
        read_gold=_reader(golds),
        store=store,
        embed_client=FakeEmbedClient(),
        model_version=_EMBEDDER,
    )

    tomb = {"cid:abc": _gold(is_tombstoned=True)}
    fake = FakeEmbedClient()
    metrics = index_gold(
        tomb, read_gold=_reader(tomb), store=store, embed_client=fake, model_version=_EMBEDDER
    )
    assert metrics.tombstoned_removed == 1
    assert metrics.indexed == 0
    assert fake.calls == []
    assert store.fetch_versions(["cid:abc"]) == {}  # gone from the collection


def test_thin_gold_is_skipped(store: MilvusIndexStore) -> None:
    """IDX-8: gold missing a required filter field is skipped + counted, never guessed."""
    golds = {"cid:thin": _gold("cid:thin", grade=None)}
    fake = FakeEmbedClient()
    metrics = index_gold(
        golds, read_gold=_reader(golds), store=store, embed_client=fake, model_version=_EMBEDDER
    )
    assert metrics.thin_skipped == 1
    assert metrics.indexed == 0
    assert fake.calls == []


def test_missing_gold_is_skipped_defensively(store: MilvusIndexStore) -> None:
    metrics = index_gold(
        ["cid:ghost"],
        read_gold=lambda _cid: None,
        store=store,
        embed_client=FakeEmbedClient(),
        model_version=_EMBEDDER,
    )
    assert metrics.model_dump() == {
        "indexed": 0,
        "skipped_unchanged": 0,
        "tombstoned_removed": 0,
        "thin_skipped": 0,
    }
