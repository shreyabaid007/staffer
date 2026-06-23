"""Tests for dsm.index.retrieve.hybrid_recall (b-002 T-003; FR-4; §6.6/AD-089).

Recall OFF (passthrough, scores None) · ON (dense ⊕ BM25 ⊕ RRF, scores populated) · RRF
determinism · recall error → exhaustive fallback. Real temp Milvus Lite db + FakeEmbedClient
(no network, NF-1).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from dsm.index.embed_client import EmbedError
from dsm.index.milvus_store import MilvusIndexStore
from dsm.index.models import CandidateIndexRecord
from dsm.index.retrieve import hybrid_recall
from dsm.models import (
    Candidate,
    CandidateSource,
    FeedbackSignals,
    FreeNow,
    Grade,
    Location,
    ProficiencyLevel,
    Skill,
)
from tests.index.fakes import FakeEmbedClient

_EMBEDDER = "BAAI/bge-base-en-v1.5"
_OFF: dict = {"index": {"recall": {"enabled": False, "top_n": 10}}}
_ON: dict = {"index": {"recall": {"enabled": True, "top_n": 10}}}


def _candidate(cid: str, skill: str = "kotlin") -> Candidate:
    return Candidate(
        email=cid,  # email == candidate_id (AD-091)
        name=cid,
        location=Location(city="Chennai"),
        availability=FreeNow(),
        skills=[Skill(name=skill, proficiency=ProficiencyLevel.EXPERT)],
        feedback=FeedbackSignals(),
        source=CandidateSource.BEACH,
    )


def _record(cid: str, skill: str, embed: FakeEmbedClient) -> CandidateIndexRecord:
    text = f"{skill} expert."
    return CandidateIndexRecord(
        candidate_id=cid,
        embed_text=text,
        dense_vector=embed.embed([text], mode="passage")[0],
        skill_set=[skill],
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
    s.upsert(
        [
            _record("cid:a", "kotlin", embed),
            _record("cid:b", "kafka", embed),
        ]
    )
    return s


class TestRecallOff:
    def test_passthrough_scores_none(self, store: MilvusIndexStore) -> None:
        candidates = [_candidate("cid:a"), _candidate("cid:b")]
        out = hybrid_recall(candidates, "kotlin expert.", store, FakeEmbedClient(), _OFF)
        assert [r.candidate_id for r in out] == ["cid:a", "cid:b"]  # order preserved
        for r in out:
            assert (r.dense_score, r.bm25_score, r.rrf_score, r.rerank_score) == (
                None,
                None,
                None,
                None,
            )

    def test_off_does_not_touch_store(self) -> None:
        """OFF must not query the store — passing a store that would error proves it isn't used."""

        class _ExplodingStore:
            def search_dense(self, *a: object, **k: object) -> list:
                raise AssertionError("store must not be queried when recall is OFF")

            def search_bm25(self, *a: object, **k: object) -> list:
                raise AssertionError("store must not be queried when recall is OFF")

        out = hybrid_recall(
            [_candidate("cid:a")],
            "kotlin",
            _ExplodingStore(),  # type: ignore[arg-type]
            FakeEmbedClient(),
            _OFF,
        )
        assert [r.candidate_id for r in out] == ["cid:a"]


class TestRecallOn:
    def test_scores_populated_and_query_mode(self, store: MilvusIndexStore) -> None:
        embed = FakeEmbedClient()
        candidates = [_candidate("cid:a"), _candidate("cid:b")]
        out = hybrid_recall(candidates, "kotlin expert.", store, embed, _ON)
        assert {r.candidate_id for r in out} <= {"cid:a", "cid:b"}
        assert out, "recall ON should return the fused pool"
        for r in out:
            assert r.rrf_score is not None  # fused score always set when ON
        # the role query was embedded in QUERY mode (asymmetric, AD-072)
        assert embed.calls and embed.calls[0][1] == "query"

    def test_rrf_deterministic(self, store: MilvusIndexStore) -> None:
        candidates = [_candidate("cid:a"), _candidate("cid:b")]
        first = hybrid_recall(candidates, "kotlin expert.", store, FakeEmbedClient(), _ON)
        second = hybrid_recall(candidates, "kotlin expert.", store, FakeEmbedClient(), _ON)
        assert [(r.candidate_id, r.rrf_score) for r in first] == [
            (r.candidate_id, r.rrf_score) for r in second
        ]


class TestRecallFallback:
    def test_embed_error_falls_back_to_exhaustive(self, store: MilvusIndexStore) -> None:
        class _FailingEmbed(FakeEmbedClient):
            def embed(self, texts: list[str], *, mode: str = "passage") -> list[list[float]]:
                raise EmbedError("modal down")

        candidates = [_candidate("cid:a"), _candidate("cid:b")]
        out = hybrid_recall(candidates, "kotlin expert.", store, _FailingEmbed(), _ON)
        assert [r.candidate_id for r in out] == ["cid:a", "cid:b"]  # exhaustive passthrough
        assert all(r.rrf_score is None for r in out)
