"""Skill taxonomy — canonical mapping + unmapped flagging (ee-ingestion §6/§13).

Deterministic, LLM-free. Maps a raw skill surface form to its canonical taxonomy id; a
skill with no match is returned verbatim and flagged ``unmapped`` so silver can queue it for
review (TX-1/TX-2). The alias map lives in ``config/taxonomy.yaml`` (tech.md rule 6: maps live
in ``config/``), read once through a cached loader.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# dsm/ingest/taxonomy.py → repo root is three levels up; config/ sits beside dsm/.
_TAXONOMY_PATH = Path(__file__).resolve().parents[2] / "config" / "taxonomy.yaml"


class Taxonomy:
    """Canonical skill resolver over an alias → canonical id map."""

    def __init__(self, alias_to_canonical: dict[str, str]) -> None:
        self._aliases = alias_to_canonical

    def canonical_skill(self, raw: str) -> tuple[str, bool]:
        """Resolve a raw skill to ``(name, unmapped)``.

        Hit → ``(canonical_id, False)``. Miss → ``(trimmed_surface_form, True)`` — the
        verbatim form is preserved for the review queue (TX-2).
        """
        normalized = raw.strip().lower()
        canonical = self._aliases.get(normalized)
        if canonical is not None:
            return (canonical, False)
        return (raw.strip(), True)


def _build_alias_map(raw: dict[str, Any]) -> dict[str, str]:
    """Invert ``{canonical: [aliases]}`` into ``{normalized_alias: canonical}``."""
    skills = raw.get("skills", {}) or {}
    alias_to_canonical: dict[str, str] = {}
    for canonical, aliases in skills.items():
        canonical_id = str(canonical).strip().lower()
        # The canonical id is itself a valid lookup key.
        alias_to_canonical[canonical_id] = canonical_id
        for alias in aliases or []:
            alias_to_canonical[str(alias).strip().lower()] = canonical_id
    return alias_to_canonical


@lru_cache(maxsize=1)
def load_taxonomy() -> Taxonomy:
    """Load and cache the skill taxonomy from ``config/taxonomy.yaml``.

    Raises:
        FileNotFoundError: if the file is missing.
        ValueError: if it does not parse to a top-level mapping.
    """
    with _TAXONOMY_PATH.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a mapping at the top of {_TAXONOMY_PATH}, got {type(data)}")
    return Taxonomy(_build_alias_map(data))
