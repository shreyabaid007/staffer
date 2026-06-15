"""Deterministic eligibility gates — pure Python, no LLM imports (AD-002).

Location is checked first, availability second. A candidate that fails location is
excluded immediately and is not checked for availability (G-OUT-2), keeping the
exclusion log to one record per candidate.

This module imports ONLY ``dsm.models`` and the stdlib — enforced by an import-linter
contract in ``pyproject.toml`` (no ``dsm.pii``, ``dsm.index``, ``dspy``, ``modal``, ``httpx``).
"""

from __future__ import annotations

from dsm.models import (
    Candidate,
    EligiblePool,
    Exclusion,
    ExclusionLog,
    ExclusionReason,
    TargetProfileScorecard,
)


def _location_passes(candidate: Candidate, scorecard: TargetProfileScorecard) -> bool:
    """Whether the candidate clears the location gate (AD-020, AD-063a).

    Args:
        candidate: the person being gated.
        scorecard: the clarified role requirements.

    Returns:
        ``True`` if co-location is not required, or the candidate's city matches the
        role's city (case-insensitive), or the candidate is ``remote_eligible``.
    """
    if not scorecard.co_location_required:
        return True
    same_city = candidate.location.city.strip().lower() == scorecard.location.city.strip().lower()
    return same_city or candidate.location.remote_eligible


def filter_candidates(
    candidates: list[Candidate],
    scorecard: TargetProfileScorecard,
) -> tuple[EligiblePool, ExclusionLog]:
    """Filter candidates through the deterministic eligibility gates.

    Location gate (AD-020, AD-063a) is applied first; availability is a temporary
    pass-all here and becomes a real gate in T-003. A candidate failing location is
    excluded with ``LOCATION_MISMATCH`` and skips the availability check (G-OUT-2).

    Args:
        candidates: the supply-sheet candidates to filter.
        scorecard: the clarified role requirements (carries location + co-location flag).

    Returns:
        ``(EligiblePool, ExclusionLog)`` — the candidates that passed and the records
        of those excluded. Never returns ``NoMatchResult`` (G-OUT-1).
    """
    eligible: list[Candidate] = []
    exclusions: list[Exclusion] = []

    for candidate in candidates:
        if not _location_passes(candidate, scorecard):
            exclusions.append(
                Exclusion(
                    candidate_email=candidate.email,
                    reason=ExclusionReason.LOCATION_MISMATCH,
                    detail=(
                        f"Candidate is in {candidate.location.city}; "
                        f"role requires {scorecard.location.city} (co-location)"
                    ),
                )
            )
            continue  # G-OUT-2: location failed → do not also check availability

        # Availability gate is a temporary pass-all (real implementation in T-003).
        eligible.append(candidate)

    return (
        EligiblePool(candidates=eligible, scorecard_id=scorecard.role_id),
        ExclusionLog(exclusions=exclusions),
    )
