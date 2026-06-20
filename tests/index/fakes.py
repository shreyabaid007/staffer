"""Test doubles for the index lane — a no-network ``EmbedClient`` (a-005 NF-1)."""

from __future__ import annotations

import hashlib
import math


class FakeEmbedClient:
    """Deterministic, in-process ``EmbedClient`` — 768-dim L2-normalized vectors, no network.

    Records every ``embed`` call as ``(texts, mode)`` so tests can assert passage mode and prove a
    skipped-unchanged run issued no new embed.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str]] = []

    def embed(self, texts: list[str], *, mode: str = "passage") -> list[list[float]]:
        self.calls.append((list(texts), mode))
        return [self._vec(text) for text in texts]

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        return [0.0 for _ in passages]

    @staticmethod
    def _vec(text: str) -> list[float]:
        """A reproducible 768-dim unit vector derived from the text (dim + norm are assertable)."""
        vals: list[float] = []
        block = 0
        while len(vals) < 768:
            digest = hashlib.sha256(f"{text}:{block}".encode()).digest()
            vals.extend(byte / 255.0 for byte in digest)
            block += 1
        vals = vals[:768]
        norm = math.sqrt(sum(v * v for v in vals)) or 1.0
        return [v / norm for v in vals]
