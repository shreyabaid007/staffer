"""Deterministic eligibility gates — pure Python, no LLM imports (AD-002).

Location is checked first, availability second. A candidate that fails location is
excluded immediately and is not checked for availability (G-OUT-2), keeping the
exclusion log to one record per candidate.

This module imports ONLY ``dsm.models`` and the stdlib — enforced by an import-linter
contract in ``pyproject.toml`` (no ``dsm.pii``, ``dsm.index``, ``dspy``, ``modal``, ``httpx``).
"""

from __future__ import annotations

from datetime import date, timedelta

from dsm.models import (
    AvailabilityState,
    Candidate,
    EligiblePool,
    Exclusion,
    ExclusionLog,
    ExclusionReason,
    FreeNow,
    NewJoiner,
    RollingOff,
    TargetProfileScorecard,
)


def effective_free_date(availability: AvailabilityState) -> date | None:
    """Return the date the candidate becomes free, or ``None`` for FreeNow.

    Shared by the availability gate and the orchestrator's near-miss builder so the
    two never drift (AD-063c). ``confidence`` on RollingOff is intentionally ignored —
    the gate uses the stated date and surfaces low confidence downstream (AD-022).

    Args:
        availability: the candidate's availability variant.

    Returns:
        ``expected_date`` for RollingOff, ``join_date`` for NewJoiner, or ``None`` for
        FreeNow (which the gate treats as always-pass and the near-miss builder skips).
    """
    match availability:
        case FreeNow():
            return None
        case RollingOff():
            return availability.expected_date
        case NewJoiner():
            return availability.join_date


def is_excluded_city(candidate: Candidate, scorecard: TargetProfileScorecard) -> bool:
    """Whether the candidate's **home city** is in the role's ``exclude_cities`` (c-007 negation).

    Case-insensitive, home-``city`` only (``onsite_cities`` willingness is not consulted). Shared
    by the location gate, the ``LOCATION_MISMATCH`` detail wording, and the near-miss skip so they
    never drift. Returns ``False`` for a candidate with no home city (``city=None``) or an empty
    ``exclude_cities`` (the default) — so the common path is untouched.
    """
    if not scorecard.exclude_cities:
        return False
    cand_key = (candidate.location.city or "").strip().casefold()
    return bool(cand_key) and cand_key in {c.strip().casefold() for c in scorecard.exclude_cities}


def _location_passes(candidate: Candidate, scorecard: TargetProfileScorecard) -> bool:
    """Whether the candidate clears the location gate (AD-086).

    Args:
        candidate: the person being gated.
        scorecard: the clarified role requirements.

    Returns:
        ``False`` if the candidate's **home city** is in ``scorecard.exclude_cities`` (c-007
        query-side negation — checked **first**, regardless of co-location: the role does not want
        a person from that city at all). Otherwise: for a **distributed** role
        (``co_location_required=False``) ``True`` iff the candidate's country matches the role's
        country; for an **onsite** role (``co_location_required=True``) ``True`` iff the role has a
        city and the candidate's home city matches it (case-insensitive) **or** that city is in the
        candidate's ``onsite_cities``. ``remote_within_country`` never clears an onsite gate.
    """
    if is_excluded_city(candidate, scorecard):
        return False
    if not scorecard.co_location_required:
        return candidate.location.country == scorecard.location.country
    # Onsite: a role with no city has nothing to match against → no candidate clears.
    role_city = scorecard.location.city
    if role_city is None:
        return False
    # Compare case-insensitively and whitespace-tolerantly (ingest already strips).
    role_key = role_city.strip().casefold()
    cand_key = (candidate.location.city or "").strip().casefold()
    onsite = {c.strip().casefold() for c in candidate.location.onsite_cities}
    return cand_key == role_key or role_key in onsite


def _availability_passes(candidate: Candidate, deadline: date) -> bool:
    """Whether the candidate clears the availability gate (AD-021, AD-022).

    Args:
        candidate: the person being gated.
        deadline: ``start_date + availability_window_days`` (computed once by the caller).

    Returns:
        ``True`` if the candidate is free now, or their effective free-date is on or
        before the deadline (``<=``, so the boundary day passes).
    """
    free_date = effective_free_date(candidate.availability)
    return free_date is None or free_date <= deadline


def filter_candidates(
    candidates: list[Candidate],
    scorecard: TargetProfileScorecard,
) -> tuple[EligiblePool, ExclusionLog]:
    """Filter candidates through the deterministic eligibility gates.

    Location gate (AD-086) is applied first; availability gate (AD-021) second.
    A candidate failing location is excluded with ``LOCATION_MISMATCH`` and skips the
    availability check (G-OUT-2), so each excluded candidate yields exactly one record.

    The availability deadline is ``scorecard.start_date + scorecard.availability_window_days``
    — read from the scorecard, never hardcoded.

    Args:
        candidates: the supply-sheet candidates to filter.
        scorecard: the clarified role requirements (location, co-location flag, start
            date, availability window).

    Returns:
        ``(EligiblePool, ExclusionLog)`` — the candidates that passed and the records
        of those excluded. Never returns ``NoMatchResult`` (G-OUT-1).
    """
    deadline = scorecard.start_date + timedelta(days=scorecard.availability_window_days)
    eligible: list[Candidate] = []
    exclusions: list[Exclusion] = []

    for candidate in candidates:
        if not _location_passes(candidate, scorecard):
            # c-007: distinguish an exclusion miss from a positive-location miss (reason enum
            # unchanged — still LOCATION_MISMATCH; only the human-readable detail branches).
            if is_excluded_city(candidate, scorecard):
                detail = f"Candidate is in {candidate.location.city}, which the role excludes"
            else:
                detail = (
                    f"Candidate is in {candidate.location.city}; "
                    f"role requires {scorecard.location.city} (co-location)"
                )
            exclusions.append(
                Exclusion(
                    candidate_email=candidate.email,
                    reason=ExclusionReason.LOCATION_MISMATCH,
                    detail=detail,
                )
            )
            continue  # G-OUT-2: location failed → do not also check availability

        if not _availability_passes(candidate, deadline):
            free_date = effective_free_date(candidate.availability)
            exclusions.append(
                Exclusion(
                    candidate_email=candidate.email,
                    reason=ExclusionReason.AVAILABILITY_MISMATCH,
                    detail=(
                        f"Available {free_date}; role deadline is {deadline} "
                        f"(start {scorecard.start_date} "
                        f"+ {scorecard.availability_window_days}d)"
                    ),
                )
            )
            continue

        eligible.append(candidate)

    return (
        EligiblePool(candidates=eligible, scorecard_id=scorecard.role_id),
        ExclusionLog(exclusions=exclusions),
    )
