"""Tests for rerank (B-002 T-008; FR-4/FR-8; AD-071)."""

from __future__ import annotations

from dsm.index.embed_client import EmbedError
from dsm.index.models import RetrievedCandidate
from dsm.index.retrieve import rerank


class _FakeEmbedClient:
    """Fake embed client returning deterministic rerank scores."""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    def embed(self, texts: list[str], *, mode: str = "passage") -> list[list[float]]:
        return [[0.0] * 4 for _ in texts]

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        if self._fail:
            raise EmbedError("test failure")
        return [1.0 / (i + 1) for i in range(len(passages))]


def _rc(cid: str, **kwargs: float | None) -> RetrievedCandidate:
    return RetrievedCandidate(candidate_id=cid, **kwargs)


class TestRerank:
    def test_scores_populated_and_sorted(self) -> None:
        candidates = [_rc("a"), _rc("b"), _rc("c")]
        embed_texts = {"a": "kotlin expert", "b": "java developer", "c": "react engineer"}
        result = rerank("kotlin", candidates, embed_texts, _FakeEmbedClient())
        assert all(rc.rerank_score is not None for rc in result)
        scores = [rc.rerank_score for rc in result]
        assert all(s is not None for s in scores)
        assert scores == sorted((s for s in scores if s is not None), reverse=True)

    def test_truncates_to_top_k(self) -> None:
        candidates = [_rc(f"c{i}") for i in range(5)]
        embed_texts = {f"c{i}": f"skill {i}" for i in range(5)}
        result = rerank("skill", candidates, embed_texts, _FakeEmbedClient(), top_k=3)
        assert len(result) == 3

    def test_empty_candidates(self) -> None:
        assert rerank("kotlin", [], {}, _FakeEmbedClient()) == []


class TestRerankError:
    def test_embed_error_returns_unranked(self) -> None:
        candidates = [_rc("a"), _rc("b")]
        embed_texts = {"a": "kotlin", "b": "java"}
        result = rerank("kotlin", candidates, embed_texts, _FakeEmbedClient(fail=True))
        assert len(result) == 2
        assert all(rc.rerank_score is None for rc in result)
        assert [rc.candidate_id for rc in result] == ["a", "b"]
