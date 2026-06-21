"""Gold-backed ``CandidateStore`` adapter (b-002; §6.0/AD-091) — lives at the CLI composition root.

The query pipeline depends only on the ``CandidateStore`` port (``dsm.models``); this adapter is
the concrete gold-backed implementation, wired in here because the CLI is the **only** layer
allowed to import ``dsm/ingest/`` (§10). It reads gold via ``goldstore`` and hydrates a candidate:

- ``skills`` exclude feedback-denied skills (``demonstrated is False``, mirrors AD-081) so
  adjacency / scoring can never credit a refuted skill; a ``MergedSkill`` with no proficiency
  hydrates to ``BEGINNER`` (the lowest floor, AD-091) so it never clears a floor it shouldn't.
- ``email`` and ``name`` carry the **pseudonymised** ``candidate_id`` — never raw identity
  (AD-091); the vault holds the real name/email, read only at final rendering (deferred).
- ``source`` is derived from the availability variant (the supply tab it came from).
- tombstoned and thin (no location/availability) gold are skipped — they cannot be gated.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from dsm.ingest.goldstore import list_gold_ids, read_gold
from dsm.ingest.models import FeedbackExtraction, GoldCandidate
from dsm.models import (
    AvailabilityState,
    Candidate,
    CandidateSource,
    FeedbackEntry,
    FeedbackSignals,
    FeedbackSource,
    NewJoiner,
    ProficiencyLevel,
    RollingOff,
    Skill,
)

# Gold feedback records four sentiment bands; the serving FeedbackEntry carries three.
_SENTIMENT: dict[str, str] = {
    "very_positive": "positive",
    "positive": "positive",
    "neutral": "neutral",
    "negative": "negative",
}


def _source_from_availability(availability: AvailabilityState) -> CandidateSource:
    """Derive the supply tab from the availability variant (they correspond 1:1)."""
    if isinstance(availability, NewJoiner):
        return CandidateSource.NEW_JOINER
    if isinstance(availability, RollingOff):
        return CandidateSource.ROLLING_OFF
    return CandidateSource.BEACH


def _feedback(items: list[FeedbackExtraction]) -> FeedbackSignals:
    """Hydrate gold feedback extractions into the serving ``FeedbackSignals`` aggregate.

    ``retention_requested`` becomes ``retention_flag`` (the AD-023 trade-off signal the score stage
    reads). Source isn't recorded on gold feedback, so it defaults to ``CLIENT`` — the score stage
    reads only ``text`` + ``retention_flag``, so the band is cosmetic here.
    """
    entries = [
        FeedbackEntry(
            source=FeedbackSource.CLIENT,
            text=item.summary,
            sentiment=_SENTIMENT.get(item.sentiment),  # type: ignore[arg-type]
            retention_flag=item.retention_requested,
        )
        for item in items
    ]
    return FeedbackSignals(entries=entries)


class GoldCandidateStore:
    """Reads ``gold/<cid>.json`` and hydrates serving ``Candidate``s (a ``CandidateStore``)."""

    def __init__(self, gold_dir: Path) -> None:
        self._gold_dir = gold_dir

    def all_ids(self) -> list[str]:
        """Every ``candidate_id`` currently on disk (sorted) — the POC hydrates the full pool."""
        return sorted(list_gold_ids(self._gold_dir))

    def latest_valid_as_of(self) -> date | None:
        """The newest supply snapshot date across gold — drives the freshness guard (AD-087).

        ``None`` when there is no dated gold (an empty / undated pool, which resolves to a no-match
        downstream rather than a freshness decision).
        """
        dates: list[date] = []
        for cid in self.all_ids():
            gold = read_gold(cid, self._gold_dir)
            if gold is not None and gold.valid_as_of is not None:
                dates.append(gold.valid_as_of)
        return max(dates, default=None)

    def get(self, candidate_ids: list[str]) -> list[Candidate]:
        """Hydrate the requested ids, skipping tombstoned / thin / missing gold."""
        candidates: list[Candidate] = []
        for cid in candidate_ids:
            gold = read_gold(cid, self._gold_dir)
            if gold is None or gold.is_tombstoned:
                continue
            hydrated = self._hydrate(gold)
            if hydrated is not None:
                candidates.append(hydrated)
        return candidates

    def _hydrate(self, gold: GoldCandidate) -> Candidate | None:
        """Project one gold entity to a serving ``Candidate``; ``None`` for a thin profile."""
        if gold.location is None or gold.availability is None:
            return None  # cannot gate without location + availability
        availability = gold.availability.value
        skills = [
            Skill(name=s.name, proficiency=s.proficiency or ProficiencyLevel.BEGINNER)
            for s in gold.skills
            if s.demonstrated is not False
        ]
        return Candidate(
            email=gold.candidate_id,  # pseudonym — never raw identity (AD-091)
            name=gold.candidate_id,
            location=gold.location.value,
            availability=availability,
            skills=skills,
            feedback=_feedback(gold.feedback),
            source=_source_from_availability(availability),
            profile_summary=" ".join(gold.projects) or None,
        )
