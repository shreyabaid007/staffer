"""Deterministic retrieval quality metrics (c-004, AD-106).

Pure functions — no LLM, no keys, no network. The DeepEval LLM-judged
wrappers live in the test file (``tests/eval/test_retrieval_quality.py``),
not here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalMetrics:
    """Bundled Recall@K + contextual precision for a single case."""

    recall_at_k: float
    contextual_precision: float
    k: int
    retrieved_ids: list[str]
    relevant_ids: list[str]


def compute_recall_at_k(
    retrieved_ids: list[str],
    relevant_ids: list[str],
    k: int,
) -> float:
    """Recall@K = |retrieved[:k] ∩ relevant| / |relevant|.

    Args:
        retrieved_ids: Ordered list of candidate IDs from the pipeline
            (post-rerank, pre-scoring).
        relevant_ids: Ground-truth relevant candidate IDs from the golden set.
        k: Cutoff (typically ``index.rerank.top_k``).

    Returns:
        Recall in [0.0, 1.0].  Vacuously 1.0 when relevant set is empty.
    """
    relevant = set(relevant_ids)
    if not relevant:
        return 1.0
    top_k = set(retrieved_ids[:k])
    return len(top_k & relevant) / len(relevant)


def compute_contextual_precision(
    retrieved_ids: list[str],
    relevant_ids: list[str],
    k: int,
) -> float:
    """Contextual precision = |relevant ∩ retrieved[:k]| / min(k, |retrieved|).

    Args:
        retrieved_ids: Ordered list of candidate IDs from the pipeline.
        relevant_ids: Ground-truth relevant candidate IDs from the golden set.
        k: Cutoff.

    Returns:
        Precision in [0.0, 1.0].  0.0 when no candidates retrieved.
    """
    top_k = list(retrieved_ids[:k])
    if not top_k:
        return 0.0
    relevant = set(relevant_ids)
    return len(set(top_k) & relevant) / len(top_k)


def compute_retrieval_metrics(
    retrieved_ids: list[str],
    relevant_ids: list[str],
    k: int,
) -> RetrievalMetrics:
    """Bundle Recall@K + contextual precision into a single result.

    Args:
        retrieved_ids: Ordered list of candidate IDs from the pipeline.
        relevant_ids: Ground-truth relevant candidate IDs from the golden set.
        k: Cutoff (typically ``index.rerank.top_k``).

    Returns:
        A ``RetrievalMetrics`` instance.
    """
    return RetrievalMetrics(
        recall_at_k=compute_recall_at_k(retrieved_ids, relevant_ids, k),
        contextual_precision=compute_contextual_precision(retrieved_ids, relevant_ids, k),
        k=k,
        retrieved_ids=list(retrieved_ids),
        relevant_ids=list(relevant_ids),
    )
