"""Modal serverless functions — BGE embedding + reranking on GPU.

Deploy:  modal deploy modal/embedder.py
Test:    DSM_MODAL_SMOKE=1 uv run pytest tests/index/test_modal_smoke.py -v

Model IDs are duplicated here because Modal containers cannot read local config
at build time.  Source of truth: config/default.yaml :: models.embedder / models.reranker
"""

from __future__ import annotations

import modal

EMBEDDER_MODEL = "BAAI/bge-base-en-v1.5"
RERANKER_MODEL = "BAAI/bge-reranker-base"
BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


def download_models() -> None:
    """Pre-download model weights into the container image layer."""
    from sentence_transformers import CrossEncoder, SentenceTransformer

    SentenceTransformer(EMBEDDER_MODEL)
    CrossEncoder(RERANKER_MODEL)


image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("sentence-transformers", "torch")
    .run_function(download_models)
)

app = modal.App("staffer-models")


@app.cls(image=image, gpu="T4", scaledown_window=300, min_containers=0)
class StafferModels:
    """Hosts the BGE embedder and cross-encoder reranker on a shared T4 GPU."""

    @modal.enter()
    def load_models(self) -> None:
        from sentence_transformers import CrossEncoder, SentenceTransformer

        self.embedder = SentenceTransformer(EMBEDDER_MODEL)
        self.reranker = CrossEncoder(RERANKER_MODEL)

    @modal.method()
    def embed(self, texts: list[str], mode: str = "passage") -> list[list[float]]:
        """Embed texts into 768-dim L2-normalized vectors.

        Args:
            texts: Capability-only passages (PII-free by construction, AD-011).
            mode: "passage" for indexing (no prefix), "query" for retrieval
                  (BGE instruction-prefixed per AD-072).

        Returns:
            List of 768-dim L2-normalized float vectors.
        """
        prompt = BGE_QUERY_INSTRUCTION if mode == "query" else None
        vectors = self.embedder.encode(
            texts,
            normalize_embeddings=True,
            batch_size=64,
            prompt=prompt,
        )
        return vectors.tolist()

    @modal.method()
    def rerank(self, query: str, passages: list[str]) -> list[float]:
        """Score query-passage pairs using the cross-encoder.

        Args:
            query: The role/scorecard query text.
            passages: Candidate capability passages to rank.

        Returns:
            List of float scores (higher = more relevant). One per passage.
        """
        pairs = [[query, p] for p in passages]
        scores = self.reranker.predict(pairs, batch_size=32)
        return scores.tolist()
