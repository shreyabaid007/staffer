"""Tests for dsm.index.models — ingest-free data contracts (a-005 T-001 + b-002; AD-091).

The gold→record projection helpers moved to ``tests/index/test_build.py`` (AD-091). What remains
here is the ingest-free part: ``RetrievedCandidate`` (b-002) + ``IndexMetrics``.
"""

from __future__ import annotations

import pytest

from dsm.index.models import IndexMetrics, RetrievedCandidate


class TestRetrievedCandidate:
    def test_scores_default_to_none(self) -> None:
        """Recall deferred (AD-089) + no rerank yet → all provenance scores None."""
        rc = RetrievedCandidate(candidate_id="cid:abc")
        assert (rc.dense_score, rc.bm25_score, rc.rrf_score, rc.rerank_score) == (
            None,
            None,
            None,
            None,
        )

    def test_carries_populated_scores(self) -> None:
        rc = RetrievedCandidate(
            candidate_id="cid:abc",
            dense_score=0.8,
            bm25_score=0.4,
            rrf_score=0.5,
            rerank_score=0.9,
        )
        assert rc.rerank_score == 0.9
        assert rc.rrf_score == 0.5


class TestIndexMetrics:
    def test_defaults_zero(self) -> None:
        m = IndexMetrics()
        assert (m.indexed, m.skipped_unchanged, m.tombstoned_removed, m.thin_skipped) == (
            0,
            0,
            0,
            0,
        )

    def test_assert_clean_passes_on_normal_counts(self) -> None:
        IndexMetrics(
            indexed=3, skipped_unchanged=2, tombstoned_removed=1, thin_skipped=1
        ).assert_clean()

    def test_assert_clean_raises_on_negative(self) -> None:
        with pytest.raises(RuntimeError, match="negative"):
            IndexMetrics(indexed=-1).assert_clean()
