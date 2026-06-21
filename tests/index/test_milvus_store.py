"""Tests for dsm.index.milvus_store — tmp Milvus Lite db, in-process, no network (T-004; AC-4).

White-box: a couple of assertions read through the store's own ``_client`` to count entities or
inspect the dense vector — the production surface stays ensure/upsert/delete/fetch_versions only.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from dsm.index.milvus_store import MilvusIndexStore
from dsm.index.models import CandidateIndexRecord
from dsm.ingest.models import Grade


def _record(
    cid: str = "cid:abc",
    *,
    gold_hash: str = "sha256:g1",
    model_version: str = "BAAI/bge-base-en-v1.5",
    skill_set: list[str] | None = None,
    city: str | None = "Chennai",
    onsite_cities: list[str] | None = None,
) -> CandidateIndexRecord:
    return CandidateIndexRecord(
        candidate_id=cid,
        embed_text="Domains: payments. kotlin expert.",
        dense_vector=[0.1] * 768,
        skill_set=["kotlin", "react"] if skill_set is None else skill_set,
        grade=Grade.LEAD_CONSULTANT,
        city=city,
        remote_within_country=True,
        onsite_cities=["Chennai"] if onsite_cities is None else onsite_cities,
        availability_type="free_now",
        availability_date=None,
        valid_as_of=date(2026, 6, 1),
        gold_hash=gold_hash,
        model_version=model_version,
    )


@pytest.fixture()
def store(tmp_path: Path) -> MilvusIndexStore:
    s = MilvusIndexStore(tmp_path / "milvus.db")
    s.ensure_collection()
    return s


def _count(store: MilvusIndexStore) -> int:
    rows = store._client.query(store._collection, filter="", output_fields=["count(*)"])
    return int(rows[0]["count(*)"])


def test_ensure_collection_is_idempotent(tmp_path: Path) -> None:
    s = MilvusIndexStore(tmp_path / "milvus.db")
    s.ensure_collection()
    s.ensure_collection()  # second call: collection exists → load, no error


def test_upsert_then_reupsert_is_one_entity(store: MilvusIndexStore) -> None:
    """IDX-5: upsert replaces by PK — re-upserting identical data leaves exactly one entity."""
    store.upsert([_record()])
    store.upsert([_record()])
    assert _count(store) == 1


def test_insert_without_sparse_bm25_auto(store: MilvusIndexStore) -> None:
    """The writer never supplies ``sparse``; the BM25 Function computes it at insert (AC-4)."""
    store.upsert([_record()])  # would raise if `sparse` were a required input
    rows = store._client.query(
        store._collection, filter='candidate_id == "cid:abc"', output_fields=["dense"]
    )
    assert len(rows[0]["dense"]) == 768


def test_delete_removes_an_id(store: MilvusIndexStore) -> None:
    store.upsert([_record()])
    assert _count(store) == 1
    store.delete(["cid:abc"])
    assert _count(store) == 0


def test_delete_absent_id_is_noop(store: MilvusIndexStore) -> None:
    store.delete(["cid:missing"])  # nothing to delete — must not raise
    store.delete([])  # empty list — must not raise
    assert _count(store) == 0


def test_fetch_versions_returns_stored_pairs(store: MilvusIndexStore) -> None:
    store.upsert(
        [
            _record("cid:aaa", gold_hash="sha256:a", model_version="emb-1"),
            _record("cid:bbb", gold_hash="sha256:b", model_version="emb-2"),
        ]
    )
    versions = store.fetch_versions(["cid:aaa", "cid:bbb"])
    assert versions == {"cid:aaa": ("sha256:a", "emb-1"), "cid:bbb": ("sha256:b", "emb-2")}


def test_fetch_versions_missing_id_absent_from_map(store: MilvusIndexStore) -> None:
    store.upsert([_record("cid:aaa")])
    versions = store.fetch_versions(["cid:aaa", "cid:nope"])
    assert "cid:aaa" in versions
    assert "cid:nope" not in versions
    assert store.fetch_versions([]) == {}


def test_remote_india_city_none_roundtrips(store: MilvusIndexStore) -> None:
    """AD-075: a city=None (Remote India) record upserts and reads back without error."""
    store.upsert([_record("cid:remote", city=None)])
    rows = store._client.query(
        store._collection, filter='candidate_id == "cid:remote"', output_fields=["city"]
    )
    assert rows[0]["city"] is None


def test_onsite_cities_array_roundtrips(store: MilvusIndexStore) -> None:
    """AD-086: the onsite_cities ARRAY<VARCHAR> upserts and reads back (incl. the empty case)."""
    store.upsert(
        [
            _record("cid:onsite", onsite_cities=["Chennai", "Pune"]),
            _record("cid:none", onsite_cities=[]),
        ]
    )
    rows = store._client.query(
        store._collection,
        filter='candidate_id in ["cid:onsite", "cid:none"]',
        output_fields=["candidate_id", "onsite_cities"],
    )
    by_id = {row["candidate_id"]: row["onsite_cities"] for row in rows}
    assert sorted(by_id["cid:onsite"]) == ["Chennai", "Pune"]
    assert list(by_id["cid:none"]) == []
