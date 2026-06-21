"""Parse demand (step 1) — Open Roles CSV → ``DemandParseOutcome`` (B-1; §6.1).

Deterministic and LLM-free. Mirrors the ingest CSV parser's banner + as-of + log-and-skip
pattern (``dsm/ingest/parse/csv.py``) but lives in ``dsm/match/`` and **does not import
``dsm/ingest/``** (NF-3) — the banner/date helpers are intentionally duplicated, not shared.

Per-row failure (unparseable ``Start``, missing ``Role ID``, empty ``Required Skills``,
column-count mismatch) is logged, skipped, and recorded in ``DemandParseOutcome.skipped`` —
never silently dropped (FR-1). A missing/unparseable **banner** blocks the run (``ValueError``):
the freshness guard (AD-087) cannot operate without ``demand_as_of``.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from pathlib import Path

import structlog

from dsm.match.models import DemandParseOutcome, OpenRolesBanner
from dsm.models import Location, OpenRole, ProficiencyLevel, SkillDepth, SkillRequirement

_log = structlog.get_logger("dsm.match.demand")

# Banner: "Open Roles - <client> - as of <date>" — the as-of marker may sit mid-line.
_BANNER_RE = re.compile(r"\bas of\b\s*(.+)", re.IGNORECASE)
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DATE_FORMATS = ("%d %B %Y", "%d %b %Y", "%B %d, %Y", "%d/%m/%Y", "%d-%m-%Y")

# Required Skills encodings (§3): a recognised proficiency qualifier → HARD + floor;
# "(nice to have)", an unknown qualifier, or a bare skill → DESIRED.
_PROFICIENCY_WORDS = {level.value for level in ProficiencyLevel}
_SKILL_QUALIFIER_RE = re.compile(r"^(?P<name>.*?)\s*\((?P<qualifier>[^)]*)\)\s*$")

_REMOTE_INDIA = "remote (india)"
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_MISSING_PRIORITY = 10**9  # roles with no/blank Priority sort last, deterministically


# ---------------------------------------------------------------------------
# Banner + date helpers (duplicated from ingest by design — NF-3)
# ---------------------------------------------------------------------------


def _banner_value(first_line: str) -> str | None:
    """Return the text after an ``as of`` marker anywhere in the line, else ``None``."""
    m = _BANNER_RE.search(first_line)
    return m.group(1) if m else None


def _parse_date(value: str) -> date | None:
    """Parse a date from ISO or a small set of formats; ``None`` if unparseable."""
    cleaned = re.sub(r"(\s*,)+\s*$", "", value)
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", cleaned).strip()
    try:
        return date.fromisoformat(cleaned)
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    iso = _ISO_DATE_RE.search(value)
    if iso is not None:
        try:
            return date.fromisoformat(iso.group(0))
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Column + cell helpers
# ---------------------------------------------------------------------------


def _norm_key(key: str) -> str:
    """Normalize a column name for case/whitespace/punctuation-insensitive lookup."""
    return _NON_ALNUM.sub(" ", key.strip().lower()).strip()


def _cell(row: dict[str, str], *names: str) -> str:
    """Return the first matching cell by normalized column name, else ``""``."""
    normalized = {_norm_key(k): v for k, v in row.items()}
    for name in names:
        value = normalized.get(_norm_key(name))
        if value is not None:
            return value.strip()
    return ""


# ---------------------------------------------------------------------------
# Field parsers
# ---------------------------------------------------------------------------


def _parse_skill_token(token: str) -> SkillRequirement | None:
    """Classify one ``Required Skills`` token into a ``SkillRequirement`` (§3 encodings).

    ``"<skill> (expert|advanced|intermediate|beginner)"`` → HARD + a ``min_proficiency`` floor;
    ``"<skill> (nice to have)"``, an unknown qualifier, or a bare ``"<skill>"`` → DESIRED.
    Returns ``None`` for an empty/qualifier-only token (no skill name).
    """
    match = _SKILL_QUALIFIER_RE.match(token)
    if match is None:
        name = token.strip().lower()
        if not name:
            return None
        return SkillRequirement(name=name, depth=SkillDepth.DESIRED)

    name = match.group("name").strip().lower()
    if not name:
        return None
    qualifier = match.group("qualifier").strip().lower()
    if qualifier in _PROFICIENCY_WORDS:
        return SkillRequirement(
            name=name, depth=SkillDepth.HARD, min_proficiency=ProficiencyLevel(qualifier)
        )
    return SkillRequirement(name=name, depth=SkillDepth.DESIRED)


def _parse_skills(raw: str) -> list[SkillRequirement]:
    """Split a ``Required Skills`` cell on ``;`` and classify each non-empty token."""
    requirements: list[SkillRequirement] = []
    for token in raw.split(";"):
        requirement = _parse_skill_token(token)
        if requirement is not None:
            requirements.append(requirement)
    return requirements


def _parse_location(raw: str) -> Location:
    """Parse a role's ``Location`` cell into the AD-086 location model.

    ``"Remote (India)"`` → ``city=None, remote_within_country=True``; a plain city → that city.
    A role never carries ``onsite_cities`` (that facet describes a candidate's willingness).
    """
    loc = raw.strip()
    is_remote_india = loc.lower() == _REMOTE_INDIA
    city = None if (is_remote_india or not loc) else loc
    return Location(city=city, remote_within_country=is_remote_india)


def _parse_priority(raw: str) -> int:
    """Parse the ``Priority`` cell to an int sort key; blank/unparseable sorts last."""
    cleaned = raw.strip()
    if not cleaned:
        return _MISSING_PRIORITY
    try:
        return int(cleaned)
    except ValueError:
        return _MISSING_PRIORITY


def _build_role(row: dict[str, str]) -> OpenRole:
    """Build one ``OpenRole`` from a CSV row, raising ``ValueError`` on a malformed row.

    Malformed (FR-1): missing ``Role ID``, unparseable/blank ``Start``, or empty
    ``Required Skills``. The caller catches the error, records it in ``skipped``, and continues.
    """
    role_id = _cell(row, "Role ID")
    if not role_id:
        raise ValueError("missing Role ID")

    start_raw = _cell(row, "Start")
    start_date = _parse_date(start_raw)
    if start_date is None:
        raise ValueError(f"unparseable Start '{start_raw}'")

    required_skills = _parse_skills(_cell(row, "Required Skills"))
    if not required_skills:
        raise ValueError("empty Required Skills")

    co_location = _cell(row, "Co-location").lower() == "yes"
    description = _cell(row, "Notes / Constraints", "Notes") or None

    return OpenRole(
        role_id=role_id,
        title=_cell(row, "Title"),
        required_skills=required_skills,
        location=_parse_location(_cell(row, "Location")),
        co_location_required=co_location,
        start_date=start_date,
        description=description,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_demand(csv_path: Path) -> DemandParseOutcome:
    """Parse an Open Roles CSV into a typed, priority-ordered ``DemandParseOutcome`` (FR-1).

    Args:
        csv_path: path to the demand-side Open Roles CSV (banner line + header + one row/role).

    Returns:
        A ``DemandParseOutcome`` whose ``roles`` are ordered by ``Priority`` ascending (ties
        broken by ``role_id``) and whose ``skipped`` lists one line per malformed row.

    Raises:
        ValueError: the banner is missing or its ``as of`` date is unparseable — the freshness
            guard cannot run without ``demand_as_of``, so the whole run is blocked (FR-1-AC-7).
    """
    data = csv_path.read_bytes()
    text = data.decode("utf-8-sig", errors="replace")
    first_line = text.splitlines()[0] if text else ""

    banner_text = _banner_value(first_line)
    demand_as_of = _parse_date(banner_text) if banner_text is not None else None
    if demand_as_of is None:
        raise ValueError(
            f"Open Roles CSV banner missing or unparseable 'as of' date: {csv_path} "
            "(the freshness guard cannot run without demand_as_of)"
        )

    banner = OpenRolesBanner(demand_as_of=demand_as_of, source_path=str(csv_path))

    # Strip the banner line so it is never treated as a header/row.
    newline = text.find("\n")
    body = text[newline + 1 :] if newline != -1 else ""
    rows = list(csv.reader(io.StringIO(body)))

    skipped: list[str] = []
    if not rows:
        return DemandParseOutcome(banner=banner, roles=[], skipped=skipped)

    header = rows[0]
    parsed: list[tuple[int, OpenRole]] = []
    for row_index, row in enumerate(rows[1:]):
        if len(row) != len(header):
            message = f"row {row_index}: column-count mismatch ({len(row)} != {len(header)})"
            _log.warning("demand_row_skipped", reason=message)
            skipped.append(message)
            continue
        row_dict = dict(zip(header, row, strict=True))
        try:
            role = _build_role(row_dict)
        except ValueError as exc:
            role_id = _cell(row_dict, "Role ID") or f"row {row_index}"
            message = f"{role_id}: {exc}"
            _log.warning("demand_row_skipped", reason=message)
            skipped.append(message)
            continue
        parsed.append((_parse_priority(_cell(row_dict, "Priority")), role))

    parsed.sort(key=lambda item: (item[0], item[1].role_id))
    return DemandParseOutcome(banner=banner, roles=[role for _, role in parsed], skipped=skipped)
