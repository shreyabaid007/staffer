"""Silver stage — deterministic ``BronzeRecord → NormalizedRecord`` (ee-ingestion §5 step 3).

Type coercion, normalization, per-sheet availability mapping, and identity resolution. No LLM,
no network: same bronze in → same silver out. Invalid coercions are logged + skipped + counted
via ``lineage`` (never silently passed). This module holds the pure normalization *helpers*;
record assembly (``normalize``/``normalize_run``) and persistence build on them.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from dsm.ingest import lineage
from dsm.ingest.models import (
    AvailabilityState,
    BronzeRecord,
    Confidence,
    Grade,
    NormalizedRecord,
    NormalizedSkill,
    SourceType,
)
from dsm.ingest.taxonomy import Taxonomy
from dsm.models import FreeNow, Location, NewJoiner, RollingOff
from dsm.pii.vault import candidate_id as derive_candidate_id

SILVER_EXTRACTOR_VERSION = "silver-v1"  # a pinned derivation version (AD-066/§11)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_SUPPLY_TYPES = {
    SourceType.SUPPLY_BEACH,
    SourceType.SUPPLY_ROLLING_OFF,
    SourceType.SUPPLY_NEW_JOINERS,
}
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


# ---------------------------------------------------------------------------
# Record assembly — normalize one BronzeRecord
# ---------------------------------------------------------------------------


def _norm_key(key: str) -> str:
    """Normalize a column name for case/whitespace/punctuation-insensitive lookup."""
    return _NON_ALNUM.sub(" ", key.strip().lower()).strip()


def _cell(raw: dict[str, str | list[str]], *names: str) -> str:
    """Return the first matching string cell by normalized column name, else ``""``."""
    normalized = {_norm_key(k): v for k, v in raw.items()}
    for name in names:
        value = normalized.get(_norm_key(name))
        if isinstance(value, str):
            return value
    return ""


def _email_for(record: BronzeRecord) -> str:
    """Pull the email/join key from a bronze record by source type."""
    if record.source_type in _SUPPLY_TYPES:
        return _cell(record.raw, "Email")
    if record.source_type is SourceType.RESUME:
        return _cell(record.raw, "email_found")
    return _cell(record.raw, "email_key")  # feedback


def _split_skills(value: str) -> list[str]:
    """Split a supply skills cell (e.g. ``"Java, Kotlin"``) into trimmed, non-empty items."""
    return [item.strip() for item in value.split(",") if item.strip()]


def _normalize_skills(
    raw_value: str,
    taxonomy: Taxonomy,
    *,
    unverified: bool,
    run_id: str,
    cid: str,
) -> list[NormalizedSkill]:
    """Resolve a skills cell to ``NormalizedSkill``s; queue any unmapped ones (TX-1/2/3)."""
    skills: list[NormalizedSkill] = []
    for surface in _split_skills(raw_value):
        name, unmapped = taxonomy.canonical_skill(surface)
        if unmapped:
            lineage.log_unmapped_skill(run_id=run_id, surface_form=name, candidate_id=cid)
        skills.append(NormalizedSkill(name=name, unmapped=unmapped, unverified=unverified))
    return skills


def _build_availability(
    record: BronzeRecord,
) -> tuple[AvailabilityState | None, list[str], str | None]:
    """Map a supply record's sheet to its ``AvailabilityState`` (AV-1/2/3).

    Returns ``(availability, warnings, skip_reason)``. ``skip_reason`` is set when a required
    discriminator date is missing/unparseable (AV-4) — the caller logs+skips+counts.
    Non-supply records have no availability.
    """
    if record.source_type is SourceType.SUPPLY_BEACH:
        return FreeNow(), [], None
    if record.source_type is SourceType.SUPPLY_ROLLING_OFF:
        expected = parse_date(_cell(record.raw, "Roll-off Date"))
        if expected is None:
            return None, [], "rolling_off row has a missing/invalid Roll-off Date"
        confidence, warnings = coerce_confidence(_cell(record.raw, "Confidence"))
        return RollingOff(expected_date=expected, confidence=confidence.value), warnings, None
    if record.source_type is SourceType.SUPPLY_NEW_JOINERS:
        join_date = parse_date(_cell(record.raw, "Join Date"))
        if join_date is None:
            return None, [], "new_joiner row has a missing/invalid Join Date"
        return NewJoiner(join_date=join_date), [], None
    return None, [], None  # resume / feedback


def normalize(
    record: BronzeRecord,
    *,
    snapshot_date: date | None,
    taxonomy: Taxonomy,
    run_id: str,
    extractor_version: str = SILVER_EXTRACTOR_VERSION,
) -> NormalizedRecord | None:
    """Normalize one ``BronzeRecord`` to a ``NormalizedRecord`` (``None`` = skipped).

    Fatal (log+skip+count, NF-3): no email (ID-4) or a missing availability discriminator
    (AV-4). Non-fatal (record still emitted): missing/unknown grade or location (LOC-4) and
    lossy location collapses surface as ``parse_warnings``.
    """
    email = _email_for(record)
    if not email.strip():
        lineage.log_invalid(
            run_id=run_id,
            reason="missing email/join key — cannot derive candidate_id",
            payload=f"{record.source_type.value} row {record.row_index}",
        )
        return None
    cid = derive_candidate_id(email)

    availability, warnings, skip_reason = _build_availability(record)
    if skip_reason is not None:
        lineage.log_invalid(
            run_id=run_id,
            reason=skip_reason,
            payload=f"{record.source_type.value} row {record.row_index} cid={cid}",
        )
        return None

    parse_warnings = list(warnings)
    grade: Grade | None = None
    location: Location | None = None
    skills: list[NormalizedSkill] = []
    raw_text: str | None = None

    if record.source_type in _SUPPLY_TYPES:
        grade, grade_warnings = parse_grade(_cell(record.raw, "Grade"))
        location, loc_warnings = parse_location(
            _cell(record.raw, "Location"), _cell(record.raw, "Chennai-open")
        )
        parse_warnings.extend(grade_warnings)
        parse_warnings.extend(loc_warnings)
        is_new_joiner = record.source_type is SourceType.SUPPLY_NEW_JOINERS
        skills = _normalize_skills(
            _cell(record.raw, "Key Skills (from CV)", "Key Skills"),
            taxonomy,
            unverified=is_new_joiner,  # AD-032: new-joiner CV skills are unverified
            run_id=run_id,
            cid=cid,
        )
    elif record.source_type is SourceType.RESUME:
        raw_text = _cell(record.raw, "text") or None
    elif record.source_type is SourceType.FEEDBACK:
        raw_text = _cell(record.raw, "raw_markdown") or None

    return NormalizedRecord(
        candidate_id=cid,
        source_type=record.source_type,
        source_hash=record.source_hash,
        valid_as_of=snapshot_date,
        grade=grade,
        location=location,
        availability=availability,
        skills=skills,
        raw_text=raw_text,
        parse_warnings=parse_warnings,
        extractor_version=extractor_version,
    )
