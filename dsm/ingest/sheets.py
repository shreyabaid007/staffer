"""Real candidate ingestion from the supply sheets (a-001-ingest-sheets).

Reads the three supply tabs (Beach / Rolling Off / New Joiners) of
``data/demand-supply.xlsx`` with openpyxl and maps each valid row to a frozen
``Candidate`` (``dsm/models.py``). Sheets-only: profile/feedback enrichment and
Open Roles mapping are out of scope for this feature.

The module is pure except for the single file read at the edge
(``ingest_candidates``); the parsing helpers below take plain values and never
touch the filesystem, network, clock or RNG (I-DET-1, ``docs/tech.md``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, datetime

from dsm.ingest.models import IngestError
from dsm.models import Location, ProficiencyLevel, Skill

_REMOTE_RE = re.compile(r"remote", re.IGNORECASE)


def _norm(value: object) -> str:
    """Normalise a header/cell label for case-insensitive matching."""
    return str(value).strip().lower()


def parse_date(cell: object) -> date:
    """Coerce a date cell to a ``date`` (I-EDGE-3).

    Accepts a ``date``, a ``datetime`` (openpyxl's native type for date cells), or
    an ISO ``YYYY-MM-DD`` string. Anything else raises ``ValueError`` (caught by the
    reader and recorded as a ``RowIssue``).
    """
    # datetime is a subclass of date — check it first.
    if isinstance(cell, datetime):
        return cell.date()
    if isinstance(cell, date):
        return cell
    if isinstance(cell, str):
        text = cell.strip()
        try:
            return datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError(f"unparseable date: {cell!r}") from exc
    raise ValueError(f"unparseable date: {cell!r}")


def parse_skills(raw: object) -> list[Skill]:
    """Split a *Key Skills* cell into normalised ``Skill`` records (I-CAND-3).

    Comma-separated, trimmed, lowercased, de-duplicated preserving first-seen
    order. Proficiency is not present in the sheets, so every skill defaults to
    ``INTERMEDIATE`` (OQ-2), to be overwritten when profiles supply real levels.
    An empty/``None`` cell yields ``[]``.
    """
    if raw is None:
        return []
    text = str(raw).strip()
    if not text:
        return []
    skills: list[Skill] = []
    seen: set[str] = set()
    for part in text.split(","):
        name = part.strip().lower()
        if not name or name in seen:
            continue
        seen.add(name)
        skills.append(Skill(name=name, proficiency=ProficiencyLevel.INTERMEDIATE))
    return skills


def parse_location(location_text: object, chennai_open: object) -> Location:
    """Map *Location* + *Chennai-open* to a ``Location`` (I-CAND-4, AD-020/063a).

    ``remote_eligible`` is ``True`` when *Chennai-open* is ``Yes`` **or** any
    location segment denotes remote (e.g. ``remote-India``, ``Remote (India)``).
    City is the first non-remote segment, or the first segment verbatim when every
    segment is remote. ``country`` defaults to ``"India"``.
    """
    text = "" if location_text is None else str(location_text).strip()
    segments = [seg.strip() for seg in text.split("/") if seg.strip()]

    open_to_chennai = chennai_open is not None and _norm(chennai_open) == "yes"
    has_remote_segment = any(_REMOTE_RE.search(seg) for seg in segments)
    remote_eligible = open_to_chennai or has_remote_segment

    city = ""
    if segments:
        non_remote = [seg for seg in segments if not _REMOTE_RE.search(seg)]
        city = non_remote[0] if non_remote else segments[0]

    return Location(city=city, country="India", remote_eligible=remote_eligible)


def _header_index(ws: object, *, sheet: str, required: Iterable[str]) -> dict[str, int]:
    """Map normalised header name → 1-based column index from the header row (row 2).

    Columns are resolved by name, not fixed position (I-LOAD-3). Raises
    ``IngestError`` naming the offending column if a required header is missing
    (I-LOAD-2).
    """
    headers: dict[str, int] = {}
    for col, cell in enumerate(ws[2], start=1):  # type: ignore[index]
        if cell.value is None:
            continue
        headers[_norm(cell.value)] = col

    missing = [name for name in required if _norm(name) not in headers]
    if missing:
        raise IngestError(f"{sheet}: missing required column(s): {', '.join(missing)}")
    return headers


def _is_blank(values: Iterable[object]) -> bool:
    """True when every cell in a data row is empty/``None`` (I-EDGE-2)."""
    return all(v is None or (isinstance(v, str) and not v.strip()) for v in values)
