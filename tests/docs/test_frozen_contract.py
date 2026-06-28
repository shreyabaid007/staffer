"""Frozen-contract snapshot — any change to dsm/models.py's public schema is a reviewable diff.

``dsm/models.py`` is the single typed interface between every module (AD-060, "FROZEN"). It has
already taken ~8 signed-off amendments and iteration 2 will touch it again. This test snapshots the
JSON schema of every public model + enum so a backwards-incompatible change can't slip in silently:
it shows up as a failing diff that must be reviewed and recorded with a superseding ADR.

Regenerate the baseline (after an intended, ADR-backed change) with::

    make contract-snapshot
"""

from __future__ import annotations

import inspect
import json
import os
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

import dsm.models as models

SNAPSHOT = Path(__file__).resolve().parent / "frozen_contract.schema.json"


def build_contract_schema() -> dict[str, object]:
    """{name: json-schema} for every BaseModel / enum defined in dsm.models, deterministically."""
    out: dict[str, object] = {}
    for name in sorted(dir(models)):
        obj = getattr(models, name)
        if not inspect.isclass(obj) or getattr(obj, "__module__", None) != "dsm.models":
            continue
        if issubclass(obj, BaseModel) and obj is not BaseModel:
            out[name] = obj.model_json_schema()
        elif issubclass(obj, Enum):
            out[name] = {"enum": [e.value for e in obj]}
    return out


def test_frozen_contract_matches_snapshot() -> None:
    current = build_contract_schema()
    if os.environ.get("UPDATE_CONTRACT_SNAPSHOT"):
        SNAPSHOT.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return
    assert SNAPSHOT.exists(), "no baseline — run `make contract-snapshot` to create it"
    saved = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    added = sorted(set(current) - set(saved))
    removed = sorted(set(saved) - set(current))
    changed = sorted(k for k in set(current) & set(saved) if current[k] != saved[k])
    assert current == saved, (
        "dsm/models.py public schema changed vs the frozen-contract snapshot (AD-060).\n"
        f"  added models/enums:   {added}\n"
        f"  removed models/enums: {removed}\n"
        f"  changed schema:       {changed}\n"
        "If intended: record an ADR, then regenerate via `make contract-snapshot`."
    )
