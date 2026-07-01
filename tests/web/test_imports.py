"""NF-2 import boundary: the web layer must not import ``dsm.pii`` (or a provider) directly.

``dsm.web`` is a composition root that reuses the CLI builders — the PII boundary lives there, not
in the web module. A direct ``dsm.pii`` import would route PII through the web edge, breaking the
``match ⊥ PII`` discipline. (Enforced here by AST scan rather than an import-linter ``forbidden``
contract, which would false-positive on the legitimate transitive ``dsm.web → dsm.cli → dsm.pii``.)
"""

from __future__ import annotations

import ast
from pathlib import Path

import dsm.web

_WEB_DIR = Path(dsm.web.__file__).resolve().parent


def _direct_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def test_web_modules_do_not_import_pii_or_provider() -> None:
    for name in ("models.py", "service.py", "app.py"):
        mods = _direct_imports(_WEB_DIR / name)
        assert not any(m == "dsm.pii" or m.startswith("dsm.pii.") for m in mods), (
            f"{name} imports dsm.pii"
        )
        assert "modal" not in mods, f"{name} imports modal directly"
        assert "httpx" not in mods, f"{name} imports httpx directly"
