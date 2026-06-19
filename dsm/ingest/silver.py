"""Silver stage — deterministic ``BronzeRecord → NormalizedRecord`` (ee-ingestion §5 step 3).

Type coercion, normalization, per-sheet availability mapping, and identity resolution. No LLM,
no network: same bronze in → same silver out. Invalid coercions are logged + skipped + counted
via ``lineage`` (never silently passed). This module holds the pure normalization *helpers*;
record assembly (``normalize``/``normalize_run``) and persistence build on them.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from dsm.ingest.models import Confidence, Grade
from dsm.models import Location

SILVER_EXTRACTOR_VERSION = "silver-v1"  # a pinned derivation version (AD-066/§11)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
)
_REMOTE_INDIA = "remote (india)"
_CONFIDENCE_ALIASES = {
    "high": Confidence.HIGH,
    "medium": Confidence.MEDIUM,
    "med": Confidence.MEDIUM,
    "low": Confidence.LOW,
}


def parse_grade(raw: str) -> tuple[Grade | None, list[str]]:
    """Map the supply ``Grade`` cell to the ``Grade`` enum (GR-1).

    Missing → ``(None, ["grade missing"])``; unrecognized → ``(None, [warning])``. The record
    is still emitted (grade is nullable); only identity/availability are fatal.
    """
    cleaned = raw.strip()
    if not cleaned:
        return None, ["grade missing"]
    key = _NON_ALNUM.sub("_", cleaned.lower()).strip("_")
    try:
        return Grade(key), []
    except ValueError:
        return None, [f"unrecognized grade '{cleaned}'"]


def parse_location(location: str, chennai_open: str) -> tuple[Location, list[str]]:
    """Build a frozen ``Location`` from the two supply columns (LOC-1/2/3/NET).

    ``city = Location`` (``None`` for ``Remote (India)``, AD-075);
    ``remote_eligible = (Location == "Remote (India)") OR (Chennai-open == "Yes")``.
    Lossy collapses are recorded as warnings (§15#3 overloading noted, not modelled).
    """
    warnings: list[str] = []
    loc = location.strip()
    open_flag = chennai_open.strip().lower() == "yes"
    is_remote_india = loc.lower() == _REMOTE_INDIA

    city = None if (is_remote_india or not loc) else loc
    remote_eligible = is_remote_india or open_flag

    if not loc:
        warnings.append("location missing")
    if is_remote_india:
        warnings.append("location 'Remote (India)' → no base city; remote_eligible=True (LOC-3)")
    if open_flag:
        warnings.append(
            "Chennai-open=Yes collapsed into remote_eligible; "
            "city-specific openness not modelled (§15#3)"
        )
    return Location(city=city, remote_eligible=remote_eligible), warnings


def parse_date(raw: str) -> date | None:
    """Parse a date from a small set of deterministic formats; ``None`` if blank/unparseable."""
    cleaned = raw.strip()
    if not cleaned:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def coerce_confidence(raw: str) -> tuple[Confidence, list[str]]:
    """Map the roll-off ``Confidence`` cell to the enum; unknown → ``low`` + a warning (AV-2)."""
    key = raw.strip().lower()
    conf = _CONFIDENCE_ALIASES.get(key)
    if conf is None:
        return Confidence.LOW, [
            f"unrecognized roll-off confidence '{raw.strip()}' → defaulted to low"
        ]
    return conf, []
