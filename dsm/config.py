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
_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "default.yaml"


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
