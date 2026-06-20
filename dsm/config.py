"""Runtime configuration loader — reads ``config/default.yaml`` (read-only, AD-064).

``config/`` is imported, never written by runtime code (``docs/structure.md``). This is
the single place that parses the YAML, so callers get one cached dict instead of scattered
file I/O. The deterministic gates do **not** use this — they read the availability window
straight off the scorecard; only the orchestrator (CLI) reads ranking config here.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# dsm/config.py → repo root is two levels up; config/ sits beside dsm/ in the checkout.
_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
_CONFIG_PATH = _CONFIG_DIR / "default.yaml"
_PROMPTS_DIR = _CONFIG_DIR / "prompts"


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    """Load and cache the runtime config from ``config/default.yaml``.

    Returns:
        The parsed YAML as a nested mapping (``weights``, ``ranking``, ``availability``,
        ``adjacency_map``, ``models``, ``logging``).

    Raises:
        FileNotFoundError: if ``config/default.yaml`` is missing.
        ValueError: if the file does not parse to a top-level mapping.
    """
    with _CONFIG_PATH.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a mapping at the top of {_CONFIG_PATH}, got {type(data)}")
    return data


@lru_cache(maxsize=8)
def load_prompt(name: str) -> str:
    """Load a versioned DSPy-signature instruction from ``config/prompts/<name>.md`` (AD-078).

    Prompts live in ``config/`` (tech.md rule 6) so a wording change is a visible diff and pairs
    with the ``enrich.prompt_version`` bump that forces re-extraction (§11). Read-only.

    Raises:
        FileNotFoundError: if the named prompt file is missing.
    """
    return (_PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()
