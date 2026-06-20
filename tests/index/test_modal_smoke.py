"""Opt-in smoke test for the deployed Modal embedder + reranker.

Run:  DSM_MODAL_SMOKE=1 uv run pytest tests/index/test_modal_smoke.py -v

Requires a deployed ``staffer-models`` app and valid Modal credentials.
Skipped by default in ``make check``.
"""

from __future__ import annotations

import math
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DSM_MODAL_SMOKE"),
    reason="Set DSM_MODAL_SMOKE=1 to run Modal integration tests",
)


@pytest.fixture(scope="module")
def client():
    from dsm.index.embed_client import ModalEmbedClient

    return ModalEmbedClient()


SAMPLE_PASSAGES = [
    (
        "Backend engineering specialist, payments domain,"
        " lead-level seniority. kotlin expert. java expert."
    ),
    "Data scientist, ML pipelines, mid-level. python expert. scikit-learn.",
    "Frontend engineer, design systems. react expert. typescript. figma.",
]

SAMPLE_QUERY = "Senior backend engineer with kotlin and payments experience"


class TestModalEmbed:
    def test_embed_returns_correct_shape(self, client) -> None:
        vectors = client.embed(SAMPLE_PASSAGES)
        assert len(vectors) == 3
        for v in vectors:
            assert len(v) == 768

    def test_embed_vectors_are_normalized(self, client) -> None:
        vectors = client.embed(SAMPLE_PASSAGES)
        for v in vectors:
            norm = math.sqrt(sum(x * x for x in v))
            assert abs(norm - 1.0) < 1e-4, f"Vector not L2-normalized: norm={norm}"

    def test_query_mode_returns_different_vectors(self, client) -> None:
        passage_vecs = client.embed(["kotlin expert"], mode="passage")
        query_vecs = client.embed(["kotlin expert"], mode="query")
        assert passage_vecs[0] != query_vecs[0], "Query prefix should produce different vectors"


class TestModalRerank:
    def test_rerank_returns_scores(self, client) -> None:
        scores = client.rerank(SAMPLE_QUERY, SAMPLE_PASSAGES)
        assert len(scores) == 3
        assert all(isinstance(s, float) for s in scores)

    def test_relevant_passage_scores_highest(self, client) -> None:
        scores = client.rerank(SAMPLE_QUERY, SAMPLE_PASSAGES)
        assert scores[0] > scores[1], "Kotlin/payments passage should score highest"
        assert scores[0] > scores[2], "Kotlin/payments passage should score highest"
