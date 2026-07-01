"""NF-1 import boundary (AST): ``guardrails ⊥ {match, pii, ingest}`` and ``match ⊥ guardrails``.

The guardrails layer is a composition-root concern wired at ``dsm.cli`` (like PII). It must never
import the spine, the PII boundary, or ingest — so a non-deterministic LLM validator can never
reach a gate or a PII decision — and the spine must never import it. (Complements the
import-linter ``forbidden`` contracts in ``pyproject.toml``; the AST scan is dependency-free and
pinpoints the offending file.)
"""

from __future__ import annotations

import ast
from pathlib import Path

import dsm.guardrails
import dsm.match

_GUARD_DIR = Path(dsm.guardrails.__file__).resolve().parent
_MATCH_DIR = Path(dsm.match.__file__).resolve().parent


def _direct_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def _imports_pkg(mods: set[str], pkg: str) -> bool:
    return any(m == pkg or m.startswith(pkg + ".") for m in mods)


def test_guardrails_does_not_import_spine_pii_ingest_or_index() -> None:
    forbidden = ("dsm.match", "dsm.pii", "dsm.ingest", "dsm.index")
    for py in sorted(_GUARD_DIR.glob("*.py")):
        mods = _direct_imports(py)
        for pkg in forbidden:
            assert not _imports_pkg(mods, pkg), f"dsm/guardrails/{py.name} imports {pkg}"


def test_match_does_not_import_guardrails() -> None:
    for py in sorted(_MATCH_DIR.glob("*.py")):
        assert not _imports_pkg(_direct_imports(py), "dsm.guardrails"), (
            f"dsm/match/{py.name} imports dsm.guardrails"
        )
