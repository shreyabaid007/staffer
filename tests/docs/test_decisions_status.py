"""Sanity checks for the decisions-status generator (scripts/decisions_status.py).

The generator is a derived view, so it can't drift — but a malformed supersession note (a
dangling or backwards "superseded by AD-N") would silently mislabel the live set. These guard that.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "decisions_status", ROOT / "scripts" / "decisions_status.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # let @dataclass resolve its own module
    spec.loader.exec_module(mod)
    return mod


def test_parses_the_decision_log_and_renders() -> None:
    ds = _load_generator()
    adrs = ds.parse_decisions()
    assert len(adrs) >= 70, f"only parsed {len(adrs)} ADRs — the entry format likely changed"
    out = ds.render(adrs)
    assert "In force" in out and "Superseded" in out


def test_supersession_links_exist_and_point_forward() -> None:
    ds = _load_generator()
    adrs = ds.parse_decisions()
    for a in adrs.values():
        for killer in a.superseded_by:
            assert killer in adrs, f"AD-{a.id:03d} is superseded by undefined AD-{killer:03d}"
            assert killer > a.id, (
                f"AD-{a.id:03d} superseded by earlier AD-{killer:03d} (backwards link)"
            )


def test_every_adr_is_in_exactly_one_bucket() -> None:
    ds = _load_generator()
    adrs = ds.parse_decisions()
    superseded = {i for i, a in adrs.items() if a.superseded_by}
    live_or_deferred = {i for i, a in adrs.items() if not a.superseded_by}
    assert superseded.isdisjoint(live_or_deferred)
    assert superseded | live_or_deferred == set(adrs)
