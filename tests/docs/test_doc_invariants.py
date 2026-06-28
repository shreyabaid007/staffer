"""Cross-document invariants — fail the build when the docs drift from reality.

These guard the exact drift classes that the iteration-1 → iteration-2 doc review had to
fix by hand: a stale ADR range, a footer that disagrees with the log, references to
ADRs/modules that no longer exist, and the always-loaded steering docs contradicting
``config/default.yaml`` (the recall-default flip that rotted ~6 files).

Design rule: **only high-confidence checks live here.** Append-only session logs legitimately
record point-in-time facts (e.g. "4 contracts", an old config value) — those are history, not
drift, so nothing here asserts against lane-file *session logs*. We check the decision log's
internal integrity, that no doc points at a non-existent ADR, and that the five small
always-loaded steering docs never contradict config. See CLAUDE.md § Doc hygiene.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DECISION = ROOT / "docs" / "decision.md"
PROGRESS = ROOT / "docs" / "progress.md"
CONFIG = ROOT / "config" / "default.yaml"

# Always-loaded steering docs — these must NEVER contradict config/code. (Lane files are
# excluded on purpose: their session logs are append-only point-in-time history.)
STEERING = [
    ROOT / "CLAUDE.md",
    ROOT / "docs" / "product.md",
    ROOT / "docs" / "tech.md",
    ROOT / "docs" / "structure.md",
    ROOT / "README.md",
]

# Docs that reference ADR ids and must not point at an undefined one.
ADR_REFERRERS = [
    DECISION,
    PROGRESS,
    *STEERING,
    ROOT / "docs" / "progress.A.md",
    ROOT / "docs" / "progress.B.md",
    ROOT / "docs" / "progress.C.md",
    ROOT / "ee-ingestion-architecture.md",
    ROOT / "ee-query-architecture.md",
]

# Modules deleted from the tree (PR #26) — must not be described as present in steering docs.
DELETED_MODULES = ["dsm/index/stub.py", "dsm/ingest/stub.py", "dsm/pii/stub.py"]

_AD_DEF = re.compile(r"^- \*\*AD-(\d+) ·", re.MULTILINE)  # one canonical definition per ADR
_AD_REF = re.compile(r"AD-(\d+)")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _defined_ad_ids() -> list[int]:
    return [int(n) for n in _AD_DEF.findall(_read(DECISION))]


# --------------------------------------------------------------------------------------
# Decision-log integrity
# --------------------------------------------------------------------------------------
def test_no_duplicate_adr_ids() -> None:
    """Each ADR is defined exactly once (the AD-097/098 ↔ AD-101/102 collision class)."""
    ids = _defined_ad_ids()
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, f"Duplicate ADR definitions in decision.md: {[f'AD-{i:03d}' for i in dupes]}"


def test_footer_next_ad_matches_max() -> None:
    """decision.md footer 'Next ADRs start at AD-NNN' == max defined + 1."""
    m = re.search(r"Next ADRs start at AD-(\d+)", _read(DECISION))
    assert m, "decision.md footer 'Next ADRs start at AD-NNN' is missing"
    nxt, mx = int(m.group(1)), max(_defined_ad_ids())
    assert nxt == mx + 1, (
        f"Footer says next AD-{nxt:03d}, but max defined is AD-{mx:03d} → expected AD-{mx + 1:03d}"
    )


# --------------------------------------------------------------------------------------
# Index ↔ decision-log consistency
# --------------------------------------------------------------------------------------
def test_progress_index_ad_range_matches_decision() -> None:
    """The index's 'current range AD-001 … AD-NNN; next starts at AD-MMM' tracks the log."""
    mx = max(_defined_ad_ids())
    m = re.search(
        r"current range AD-\d+ .{1,5} AD-(\d+); next starts at AD-(\d+)", _read(PROGRESS)
    )
    assert m, "progress.md 'current range AD-… ; next starts at AD-…' line missing or reworded"
    hi, nxt = int(m.group(1)), int(m.group(2))
    assert hi == mx, f"progress.md AD range top AD-{hi:03d} != decision.md max AD-{mx:03d}"
    assert nxt == mx + 1, f"progress.md 'next AD-{nxt:03d}' != AD-{mx + 1:03d}"


# --------------------------------------------------------------------------------------
# No dangling ADR references anywhere
# --------------------------------------------------------------------------------------
def test_no_dangling_adr_references() -> None:
    """Every AD-NNN written in any doc is defined in decision.md (or is the footer's next id)."""
    allowed = set(_defined_ad_ids())
    allowed.add(max(_defined_ad_ids()) + 1)  # the 'next ADRs start at' placeholder
    dangling: dict[str, list[str]] = {}
    for doc in ADR_REFERRERS:
        if not doc.exists():
            continue
        for n in {int(x) for x in _AD_REF.findall(_read(doc))}:
            if n not in allowed:
                dangling.setdefault(f"AD-{n:03d}", []).append(doc.name)
    assert not dangling, f"References to undefined ADRs: {dangling}"


# --------------------------------------------------------------------------------------
# Steering docs must not contradict config or reference deleted modules
# --------------------------------------------------------------------------------------
def test_steering_docs_do_not_restate_a_stale_recall_default() -> None:
    """If config ships recall ON, no steering doc may *restate* it as ``false`` (AD-109 class).

    Deliberately matches only the **structured** value form (``index.recall.enabled = false`` /
    ``: false`` / ``=false``), not loose prose — high precision, no false positives on meta-text
    that merely *names* the key. Loose-prose restatements are covered by the CLAUDE.md policy.
    """
    cfg = yaml.safe_load(_read(CONFIG))
    enabled = bool(cfg["index"]["recall"]["enabled"])
    opposite = "false" if enabled else "true"
    pattern = rf"index\.recall\.enabled['`\"\s]*[:=]\s*{opposite}\b"
    hits = {
        doc.name
        for doc in STEERING
        if doc.exists() and re.search(pattern, _read(doc), re.IGNORECASE)
    }
    state = "ON" if enabled else "OFF"
    assert not hits, (
        f"config ships recall {state} (index.recall.enabled={enabled}), but these steering docs "
        f"restate the opposite value — reference the config key, don't restate it: {sorted(hits)}"
    )


def test_steering_docs_do_not_reference_deleted_modules() -> None:
    """Deleted modules (PR #26 stubs) must not be described as present in steering docs."""
    hits: dict[str, list[str]] = {}
    for doc in STEERING:
        if not doc.exists():
            continue
        text = _read(doc)
        for mod in DELETED_MODULES:
            if mod in text:
                hits.setdefault(doc.name, []).append(mod)
    assert not hits, f"Steering docs reference deleted modules: {hits}"
