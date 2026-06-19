"""Gold-layer persistence — one ``gold/<candidate_id>.json`` per consultant (§4/§5; GS-1..4).

Immutable, content-addressed by ``candidate_id``, written atomically (temp+rename) — the bronze/
silver writer pattern. ``gold_hash`` (re-exported from ``merge``) is the change-detection key the
index phase uses to re-embed only what changed (GS-2). Gold is **gitignored** (GS-3): it carries
vault refs + verbatim evidence quotes that can include client-org names. Identity is vault refs
only — never raw name/email (GS-4).
"""

from __future__ import annotations

import os
from pathlib import Path

from dsm.ingest.merge import gold_content_hash as gold_hash  # GS-2: single hash impl, re-exported
from dsm.ingest.models import GoldCandidate

__all__ = ["gold_hash", "write_gold", "read_gold", "list_gold_ids"]

_CID_PREFIX = "cid:"


def _gold_path(gold_root: Path, candidate_id: str) -> Path:
    """``gold/<hex>.json`` — the ``cid:`` prefix is stripped for the filename (mirrors silver)."""
    return gold_root / f"{candidate_id.removeprefix(_CID_PREFIX)}.json"


def write_gold(candidate: GoldCandidate, gold_root: Path) -> Path:
    """Persist one ``GoldCandidate`` to ``gold/<cid>.json`` atomically (GS-1); idempotent."""
    dest = _gold_path(gold_root, candidate.candidate_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(candidate.model_dump_json(), encoding="utf-8")
    os.replace(tmp, dest)
    return dest


def read_gold(candidate_id: str, gold_root: Path) -> GoldCandidate | None:
    """Read one gold entity by ``candidate_id``, or ``None`` if not present."""
    path = _gold_path(gold_root, candidate_id)
    if not path.is_file():
        return None
    return GoldCandidate.model_validate_json(path.read_text(encoding="utf-8"))


def list_gold_ids(gold_root: Path) -> set[str]:
    """The ``candidate_id`` set currently on disk — the **prior** set for reconciliation (RC-1)."""
    if not gold_root.is_dir():
        return set()
    return {f"{_CID_PREFIX}{p.stem}" for p in gold_root.glob("*.json")}
