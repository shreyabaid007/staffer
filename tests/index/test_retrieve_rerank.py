"""Tests for dsm.index.retrieve.rerank (b-002 T-004; FR-5; §6.7/AD-071).

Cross-encoder ordering · truncation to top_k · error → unranked passthrough. Real temp Milvus
Lite db + a scripted no-network rerank client (NF-1).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from dsm.index.embed_client import EmbedError
from dsm.index.milvus_store import MilvusIndexStore
from dsm.index.models import CandidateIndexRecord, RetrievedCandidate
from dsm.index.retrieve import rerank
from dsm.models import Grade
from tests.index.fakes import FakeEmbedClient

_EMBEDDER = "BAAI/bge-base-en-v1.5"


class _ScriptedRerank(FakeEmbedClient):
    """A FakeEmbedClient whose rerank returns a preset score per passage (by lookup)."""

    def __init__(self, by_passage: dict[str, float]) -> None:
        super().__init__()
        self._by_passage = by_passage

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        return [self._by_passage.get(p, 0.0) for p in passages]


class _FailingRerank(FakeEmbedClient):
    def rerank(self, query: str, passages: list[str]) -> list[float]:
        raise EmbedError("reranker down")


def _record(cid: str, text: str, embed: FakeEmbedClient) -> CandidateIndexRecord:
    return CandidateIndexRecord(
        candidate_id=cid,
        embed_text=text,
        dense_vector=embed.embed([text], mode="passage")[0],
        skill_set=[text.split()[0]],
        grade=Grade.LEAD_CONSULTANT,
        city="Chennai",
        remote_within_country=False,
        onsite_cities=[],
        availability_type="free_now",
        availability_date=None,
        valid_as_of=date(2026, 6, 1),
        gold_hash="sha256:g1",
        model_version=_EMBEDDER,
    )


@pytest.fixture
def store(tmp_path: Path) -> MilvusIndexStore:
    s = MilvusIndexStore(str(tmp_path / "milvus.db"), "candidates", dim=768, metric="IP")
    s.ensure_collection()
    embed = FakeEmbedClient()
    s.upsert([_record("cid:a", "kotlin passage", embed), _record("cid:b", "kafka passage", embed)])
    return s


def _retrieved(*cids: str) -> list[RetrievedCandidate]:
    return [RetrievedCandidate(candidate_id=c) for c in cids]


class TestRerank:
    def test_orders_by_cross_encoder_score(self, store: MilvusIndexStore) -> None:
        client = _ScriptedRerank({"kotlin passage": 0.2, "kafka passage": 0.9})
        out = rerank("kotlin expert.", _retrieved("cid:a", "cid:b"), store, client, top_k=10)
        assert [r.candidate_id for r in out] == ["cid:b", "cid:a"]  # higher score first
        assert out[0].rerank_score == 0.9
        assert out[1].rerank_score == 0.2

    def test_truncates_to_top_k(self, store: MilvusIndexStore) -> None:
        client = _ScriptedRerank({"kotlin passage": 0.2, "kafka passage": 0.9})
        out = rerank("kotlin expert.", _retrieved("cid:a", "cid:b"), store, client, top_k=1)
        assert [r.candidate_id for r in out] == ["cid:b"]  # only the top survives

    def test_error_returns_unranked_no_truncation(self, store: MilvusIndexStore) -> None:
        pool = _retrieved("cid:a", "cid:b")
        out = rerank("kotlin expert.", pool, store, _FailingRerank(), top_k=1)
        assert [r.candidate_id for r in out] == ["cid:a", "cid:b"]  # unranked, NOT truncated
        assert all(r.rerank_score is None for r in out)

    def test_empty_pool(self, store: MilvusIndexStore) -> None:
        assert rerank("q", [], store, _ScriptedRerank({}), top_k=5) == []
