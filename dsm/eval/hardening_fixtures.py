"""Typed loaders for the c-010 eval-hardening fixtures (AD-XXX).

Two labelled corpora, both signed-off-gated like the golden set (:mod:`dsm.eval.golden_set`):

- **Injection corpus** (`tests/fixtures/injection_corpus.json`) — prompt-injection payloads for the
  red-team eval (:mod:`dsm.eval.red_team`).
- **Guardrail corpus** (`tests/fixtures/guardrail_corpus.json`) — labelled attack+benign text per
  detector category for detector validation (:mod:`dsm.eval.guardrail_validation`).

Fairness counterfactuals are *transformations* (proxy swaps), not data, so they are built in the
the fairness test rather than loaded here.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel

from dsm.eval.red_team import InjectionCase

logger = logging.getLogger(__name__)

_FIXTURES = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures"


class CorpusMeta(BaseModel):
    """Provenance for a labelled corpus (mirrors the golden-set meta)."""

    labeller: str
    label_date: str
    review_status: str
    notes: str = ""

    @property
    def is_signed_off(self) -> bool:
        return self.review_status == "signed_off"


class GuardrailCorpusItem(BaseModel):
    """One labelled item: ``unsafe=True`` means the detector for ``category`` SHOULD flag it."""

    category: str
    text: str
    unsafe: bool


def _read_meta(data: dict, name: str) -> CorpusMeta:
    meta = CorpusMeta.model_validate(data["_meta"])
    if not meta.is_signed_off:
        logger.warning(
            "Corpus %s review_status=%r — labels are draft, not trusted", name, meta.review_status
        )
    return meta


def load_injection_corpus(path: Path | None = None) -> tuple[CorpusMeta, list[InjectionCase]]:
    """Load the prompt-injection corpus. Returns ``(meta, cases)``."""
    target = path or _FIXTURES / "injection_corpus.json"
    if not target.exists():
        raise FileNotFoundError(f"Fixture not found: {target}")
    data = json.loads(target.read_text(encoding="utf-8"))
    meta = _read_meta(data, target.name)
    cases = [InjectionCase(**c) for c in data["cases"]]
    return meta, cases


def load_guardrail_corpus(
    path: Path | None = None,
) -> tuple[CorpusMeta, list[GuardrailCorpusItem]]:
    """Load the labelled guardrail-detector corpus. Returns ``(meta, items)``."""
    target = path or _FIXTURES / "guardrail_corpus.json"
    if not target.exists():
        raise FileNotFoundError(f"Fixture not found: {target}")
    data = json.loads(target.read_text(encoding="utf-8"))
    meta = _read_meta(data, target.name)
    items = [GuardrailCorpusItem.model_validate(i) for i in data["items"]]
    return meta, items
