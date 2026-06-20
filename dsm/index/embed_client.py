"""Typed client for the Modal-hosted embedder and reranker (AD-074/AD-071).

The ``EmbedClient`` protocol is the boundary between ``dsm.index`` and the
Modal deployment.  Tests inject a mock; production uses ``ModalEmbedClient``.
"""

from __future__ import annotations

from typing import Protocol

import modal


class EmbedError(Exception):
    """Raised when the Modal embedding/reranking call fails."""


class EmbedClient(Protocol):
    """Protocol for embedding and reranking — injectable for testing."""

    def embed(self, texts: list[str], *, mode: str = "passage") -> list[list[float]]:
        """Return 768-dim L2-normalized vectors for each text."""
        ...

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        """Return relevance scores (higher = better) for each passage."""
        ...


class ModalEmbedClient:
    """Calls the deployed ``staffer-models`` Modal app."""

    def __init__(self) -> None:
        self._cls = modal.Cls.from_name("staffer-models", "StafferModels")

    def embed(self, texts: list[str], *, mode: str = "passage") -> list[list[float]]:
        """Embed texts via the Modal BGE embedder."""
        try:
            return self._cls().embed.remote(texts, mode)
        except modal.exception.Error as exc:
            raise EmbedError(f"Modal embed call failed: {exc}") from exc

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        """Rerank passages via the Modal cross-encoder."""
        try:
            return self._cls().rerank.remote(query, passages)
        except modal.exception.Error as exc:
            raise EmbedError(f"Modal rerank call failed: {exc}") from exc
