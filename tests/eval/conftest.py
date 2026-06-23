"""Eval-suite conftest: live-provider guard for ``eval_offline`` tests (R-12, AD-094).

Patches the live LM constructor and Modal embed client to raise in offline evals,
ensuring cassette-backed tests never accidentally hit the network.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _block_live_providers(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Block live providers in eval_offline tests (R-12)."""
    if "eval_offline" not in [m.name for m in request.node.iter_markers()]:
        return

    def _raise(*a, **kw):
        raise RuntimeError("live provider called in offline eval")

    monkeypatch.setattr("dsm.index.embed_client.ModalEmbedClient.__init__", _raise)
    monkeypatch.setattr("dsm.match.clarify.make_clarify_predictor", _raise)
    monkeypatch.setattr("dsm.match.score.make_score_predictor", _raise)


def has_keys() -> bool:
    """Check if OpenRouter + Modal API keys are available for live eval."""
    return bool(os.environ.get("OPENROUTER_API_KEY")) and bool(os.environ.get("MODAL_TOKEN_ID"))
