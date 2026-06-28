#!/usr/bin/env python
"""Print the *currently in-force* decisions by parsing ``docs/decision.md``.

``decision.md`` is an append-only log: a decision is changed by adding a superseding entry
(or an inline "superseded by AD-N" note), never by editing the old one. So "what the log
contains" is not "what is true now" — to orient, an agent would otherwise have to replay
100+ ADRs and track supersession in its head (exactly how the stale AD-089 "recall OFF"
note survived next to AD-109).

This derives the live set instead. It is a **generated view** — it stores nothing, so it
cannot drift. Run::

    make decisions-status            # or: uv run python scripts/decisions_status.py

``parse_decisions`` is imported by ``tests/docs`` to assert supersession links are sane.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

DECISION = Path(__file__).resolve().parents[1] / "docs" / "decision.md"

# A canonical ADR entry: "- **AD-NNN · Title** — Status — Decision …"
_HEAD = re.compile(r"^- \*\*AD-(\d+) · (.+?)\*\* — (.+?) —", re.MULTILINE)
# Supersession is read ONLY from the victim-side note "superseded … by … AD-N" that sits on the
# dead ADR's own entry. The active "supersedes AD-M" direction is deliberately NOT parsed: it is
# usually a *partial* supersession ("supersedes AD-060's EvidenceCitation shape") whose AD ref
# bleeds across "amends (AD-x)" mentions — flagging the whole ADR dead from it mislabels in-force
# ADRs (AD-060 is the frozen contract). Precision over recall: a wrong "superseded" is worse than
# a missed one. Convention: mark a fully-dead ADR with an inline "*(superseded by AD-N)*" note.
_SUPERSEDED_BY = re.compile(r"superseded\b[^.]*?\bby\b[^.]*?AD-(\d+)", re.IGNORECASE)
# An ADR entry ends at the next ADR, or at any section header / banner / rule — so prose
# *between* entries (e.g. a "## Gating rules" banner that names a supersession) is never
# mis-attributed to the preceding ADR.
_BOUNDARY = re.compile(r"\n(?:#{1,6} |> |---)")


@dataclass
class Adr:
    id: int
    title: str
    status: str
    superseded_by: set[int] = field(default_factory=set)


def _norm_status(raw: str) -> str:
    """First word(s) of the status marker, dropping any inline ``*(superseded …)*`` tail."""
    return re.split(r"[*(]", raw, maxsplit=1)[0].strip()


def parse_decisions(text: str | None = None) -> dict[int, Adr]:
    """Parse ``decision.md`` into ``{ad_id: Adr}`` with the supersession graph resolved.

    Supersession is read from both directions — an ADR's own "superseded by AD-N" note and
    another ADR's "supersedes AD-M" note — so it does not matter which side records it.
    """
    text = text if text is not None else DECISION.read_text(encoding="utf-8")
    heads = list(_HEAD.finditer(text))
    adrs: dict[int, Adr] = {}
    blocks: dict[int, str] = {}
    for i, h in enumerate(heads):
        ad_id = int(h.group(1))
        nxt_head = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        nxt_bound = _BOUNDARY.search(text, h.end())
        end = min(nxt_head, nxt_bound.start() if nxt_bound else len(text))
        adrs[ad_id] = Adr(id=ad_id, title=h.group(2).strip(), status=_norm_status(h.group(3)))
        blocks[ad_id] = text[h.start() : end]
    # Resolve supersession from the victim-side note on each ADR's own entry only.
    for ad_id, block in blocks.items():
        for killer in _SUPERSEDED_BY.findall(block):
            if int(killer) in adrs:
                adrs[ad_id].superseded_by.add(int(killer))
    return adrs


def render(adrs: dict[int, Adr]) -> str:
    live, superseded, deferred = [], [], []
    for a in sorted(adrs.values(), key=lambda x: x.id):
        if a.superseded_by:
            superseded.append(a)
        elif a.status.lower().startswith("defer"):
            deferred.append(a)
        else:
            live.append(a)
    lines = [
        f"# Decisions currently in force — derived from docs/decision.md ({len(adrs)} ADRs total)",
        "",
        f"## In force ({len(live)})",
    ]
    lines += [f"  AD-{a.id:03d} · {a.title}  [{a.status}]" for a in live]
    lines += ["", f"## Superseded ({len(superseded)}) — kept for history, NOT current truth"]
    for a in superseded:
        by = ", ".join(f"AD-{k:03d}" for k in sorted(a.superseded_by))
        lines.append(f"  AD-{a.id:03d} · {a.title}  → superseded by {by}")
    lines += ["", f"## Deferred ({len(deferred)})"]
    lines += [f"  AD-{a.id:03d} · {a.title}" for a in deferred]
    return "\n".join(lines)


if __name__ == "__main__":
    print(render(parse_decisions()))
