"""Tests for hybrid_recall (B-002 T-007; FR-3; AD-089)."""

from __future__ import annotations

from dsm.index.embed_client import EmbedError
from dsm.index.retrieve import hybrid_recall
from dsm.models import (
    Candidate,
    CandidateSource,
    EligiblePool,
    FeedbackSignals,
    FreeNow,
    Location,
    ProficiencyLevel,
    Skill,
)


def _candidate(email: str, skills: list[str] | None = None) -> Candidate:
    return Candidate(
        email=email,
        name="Test",
        location=Location(city="Chennai"),
        availability=FreeNow(),
        skills=[Skill(name=s, proficiency=ProficiencyLevel.ADVANCED) for s in (skills or [])],
        feedback=FeedbackSignals(),
        source=CandidateSource.BEACH,
        profile_summary=f"Expert in {', '.join(skills or ['nothing'])}.",
    )


def _pool(*candidates: Candidate) -> EligiblePool:
    return EligiblePool(candidates=list(candidates), scorecard_id="ROLE-TEST")


class _FakeEmbedClient:
    """Fake embed client returning deterministic vectors and scores."""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    def embed(self, texts: list[str], *, mode: str = "passage") -> list[list[float]]:
        if self._fail:
            raise EmbedError("test failure")
        return [[float(i)] * 4 for i in range(len(texts))]

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        if self._fail:
            raise EmbedError("test failure")
        return [1.0 / (i + 1) for i in range(len(passages))]


class TestRecallOff:
    def test_passthrough_all_candidates(self) -> None:
        a = _candidate("a@x.com", ["kotlin"])
        b = _candidate("b@x.com", ["java"])
        results = hybrid_recall(_pool(a, b), "kotlin expert")
        assert len(results) == 2
        assert results[0].candidate_id == "a@x.com"
        assert results[1].candidate_id == "b@x.com"

    def test_passthrough_scores_none(self) -> None:
        a = _candidate("a@x.com", ["kotlin"])
        results = hybrid_recall(_pool(a), "kotlin expert")
        rc = results[0]
        assert rc.dense_score is None
        assert rc.bm25_score is None
        assert rc.rrf_score is None
        assert rc.rerank_score is None

    def test_empty_pool_returns_empty(self) -> None:
        results = hybrid_recall(_pool(), "kotlin expert")
        assert results == []


class TestRecallOn:
    def test_fused_scores_populated(self) -> None:
        a = _candidate("a@x.com", ["kotlin"])
        b = _candidate("b@x.com", ["java"])
        results = hybrid_recall(
            _pool(a, b),
            "kotlin expert",
            embed_client=_FakeEmbedClient(),
            enabled=True,
        )
        assert len(results) == 2
        for rc in results:
            assert rc.dense_score is not None
            assert rc.bm25_score is not None
            assert rc.rrf_score is not None

    def test_top_n_truncation(self) -> None:
        candidates = [_candidate(f"c{i}@x.com", ["kotlin"]) for i in range(5)]
        results = hybrid_recall(
            _pool(*candidates),
            "kotlin expert",
            embed_client=_FakeEmbedClient(),
            enabled=True,
            top_n=3,
        )
        assert len(results) == 3

    def test_rrf_determinism(self) -> None:
        a = _candidate("a@x.com", ["kotlin"])
        b = _candidate("b@x.com", ["java"])
        pool = _pool(a, b)
        r1 = hybrid_recall(pool, "kotlin", embed_client=_FakeEmbedClient(), enabled=True)
        r2 = hybrid_recall(pool, "kotlin", embed_client=_FakeEmbedClient(), enabled=True)
        assert [rc.candidate_id for rc in r1] == [rc.candidate_id for rc in r2]
        assert [rc.rrf_score for rc in r1] == [rc.rrf_score for rc in r2]


class TestRecallEmbedError:
    def test_embed_error_falls_back_to_passthrough(self) -> None:
        a = _candidate("a@x.com", ["kotlin"])
        b = _candidate("b@x.com", ["java"])
        results = hybrid_recall(
            _pool(a, b),
            "kotlin expert",
            embed_client=_FakeEmbedClient(fail=True),
            enabled=True,
        )
        assert len(results) == 2
        for rc in results:
            assert rc.dense_score is None
            assert rc.bm25_score is None
            assert rc.rrf_score is None
