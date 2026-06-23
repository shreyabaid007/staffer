"""Retrieval quality tests — deterministic Recall@K + contextual precision (c-004, AD-106).

Deterministic tests are ``eval_offline`` (no LLM, no keys). DeepEval
LLM-judged retrieval metrics are ``eval_live`` (key-gated, signed-off labels only).
All run under ``make eval`` only — never ``make check``.
"""

from __future__ import annotations

import pytest

from dsm.eval.retrieval_quality import (
    compute_contextual_precision,
    compute_recall_at_k,
    compute_retrieval_metrics,
)

# ---------------------------------------------------------------------------
# Deterministic Recall@K
# ---------------------------------------------------------------------------


@pytest.mark.eval_offline
class TestRecallAtK:
    def test_recall_perfect_when_all_relevant_retrieved(self) -> None:
        relevant = ["a", "b", "c"]
        retrieved = ["a", "b", "c", "d", "e"]
        assert compute_recall_at_k(retrieved, relevant, k=5) == 1.0

    def test_recall_partial_when_some_missed(self) -> None:
        relevant = ["a", "b", "c", "d"]
        retrieved = ["a", "b", "x", "y"]
        assert compute_recall_at_k(retrieved, relevant, k=4) == 0.5

    def test_recall_zero_when_none_retrieved(self) -> None:
        relevant = ["a", "b"]
        retrieved = ["x", "y", "z"]
        assert compute_recall_at_k(retrieved, relevant, k=3) == 0.0

    def test_recall_vacuous_when_relevant_empty(self) -> None:
        assert compute_recall_at_k(["a", "b"], [], k=5) == 1.0

    def test_recall_respects_k_cutoff(self) -> None:
        relevant = ["a", "b"]
        retrieved = ["x", "y", "a", "b"]
        assert compute_recall_at_k(retrieved, relevant, k=2) == 0.0
        assert compute_recall_at_k(retrieved, relevant, k=4) == 1.0

    def test_recall_with_duplicates_in_retrieved(self) -> None:
        relevant = ["a", "b"]
        retrieved = ["a", "a", "b"]
        assert compute_recall_at_k(retrieved, relevant, k=3) == 1.0


# ---------------------------------------------------------------------------
# Deterministic contextual precision
# ---------------------------------------------------------------------------


@pytest.mark.eval_offline
class TestContextualPrecision:
    def test_precision_perfect_when_all_retrieved_relevant(self) -> None:
        relevant = ["a", "b", "c"]
        retrieved = ["a", "b", "c"]
        assert compute_contextual_precision(retrieved, relevant, k=3) == 1.0

    def test_precision_degraded_with_irrelevant(self) -> None:
        relevant = ["a"]
        retrieved = ["a", "x", "y", "z"]
        assert compute_contextual_precision(retrieved, relevant, k=4) == 0.25

    def test_precision_zero_when_no_relevant_in_top_k(self) -> None:
        relevant = ["a", "b"]
        retrieved = ["x", "y", "z"]
        assert compute_contextual_precision(retrieved, relevant, k=3) == 0.0

    def test_precision_zero_when_empty_retrieved(self) -> None:
        assert compute_contextual_precision([], ["a", "b"], k=5) == 0.0

    def test_precision_respects_k_cutoff(self) -> None:
        relevant = ["a"]
        retrieved = ["x", "a"]
        assert compute_contextual_precision(retrieved, relevant, k=1) == 0.0
        assert compute_contextual_precision(retrieved, relevant, k=2) == 0.5


# ---------------------------------------------------------------------------
# Bundled metrics
# ---------------------------------------------------------------------------


@pytest.mark.eval_offline
class TestRetrievalMetrics:
    def test_bundle_returns_both_metrics(self) -> None:
        relevant = ["a", "b", "c"]
        retrieved = ["a", "b", "x"]
        m = compute_retrieval_metrics(retrieved, relevant, k=3)
        assert m.recall_at_k == pytest.approx(2.0 / 3.0)
        assert m.contextual_precision == pytest.approx(2.0 / 3.0)
        assert m.k == 3
        assert m.retrieved_ids == ["a", "b", "x"]
        assert m.relevant_ids == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Golden-set integration (requires golden_set.json)
# ---------------------------------------------------------------------------


@pytest.mark.eval_offline
class TestGoldenSetRetrievalMetrics:
    def test_golden_set_loads_for_retrieval(self) -> None:
        """Verify the golden set has cases with non-empty relevant sets."""
        from dsm.eval.golden_set import load_golden_set

        gs = load_golden_set()
        cases_with_relevant = [c for c in gs.cases if c.expected_relevant_set]
        assert len(cases_with_relevant) >= 3, (
            f"Expected at least 3 cases with relevant sets, got {len(cases_with_relevant)}"
        )
