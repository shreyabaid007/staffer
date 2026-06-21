"""GoldCandidateStore — composition-root adapter (AD-091).

Lives in ``dsm/cli/`` (the composition root) because it bridges ``dsm.ingest`` (gold layer)
to ``dsm.models.Candidate`` (serving contract). Query-time code in ``dsm/match/`` and
``dsm/index/`` depends on the ``CandidateStore`` protocol only — never on this class directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import structlog

from dsm.ingest.goldstore import list_gold_ids, read_gold
from dsm.ingest.models import GoldCandidate, MergedSkill
from dsm.models import (
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

_Sentiment = Literal["positive", "neutral", "negative"]

_log = structlog.get_logger("dsm.cli.candidate_store")


def _skill_from_merged(ms: MergedSkill) -> Skill:
    """Project a gold MergedSkill to a serving Skill."""
    return Skill(
        name=ms.name,
        proficiency=ms.proficiency or ProficiencyLevel.INTERMEDIATE,
    )


def _source_from_availability(gold: GoldCandidate) -> CandidateSource:
    """Infer CandidateSource from the gold availability variant."""
    if gold.availability is None:
        return CandidateSource.BEACH
    avail = gold.availability.value
    if isinstance(avail, NewJoiner):
        return CandidateSource.NEW_JOINER
    if isinstance(avail, RollingOff):
        return CandidateSource.ROLLING_OFF
    return CandidateSource.BEACH


def _feedback_from_gold(gold: GoldCandidate) -> FeedbackSignals:
    """Project gold feedback extractions to serving FeedbackSignals."""
    entries: list[FeedbackEntry] = []
    for fb in gold.feedback:
        source = (
            FeedbackSource.CLIENT
            if fb.retention_requested or fb.rejection_requested
            else FeedbackSource.INTERNAL_EE
        )
        sentiment_map: dict[str, _Sentiment] = {
            "very_positive": "positive",
            "positive": "positive",
            "neutral": "neutral",
            "negative": "negative",
        }
        entries.append(
            FeedbackEntry(
                source=source,
                text=fb.summary,
                sentiment=sentiment_map.get(fb.sentiment),
                retention_flag=fb.retention_requested,
            )
        )
    return FeedbackSignals(entries=entries)


def _hydrate(gold: GoldCandidate) -> Candidate | None:
    """Hydrate a serving Candidate from a GoldCandidate.

    Returns None if the gold entity lacks the minimum required fields.
    Name and email are carried as vault refs — never sent to an LLM.
    """
    if gold.is_tombstoned:
        return None
    if gold.location is None or gold.availability is None:
        return None

    profile_parts: list[str] = []
    if gold.domains:
        domain_names = sorted(d.value for d in gold.domains)
        profile_parts.append("Domains: " + ", ".join(domain_names) + ".")
    if gold.projects:
        profile_parts.extend(gold.projects)

    return Candidate(
        email=gold.email_vault_ref,
        name=gold.name_vault_ref,
        location=gold.location.value,
        availability=gold.availability.value,
        skills=[_skill_from_merged(ms) for ms in gold.skills],
        feedback=_feedback_from_gold(gold),
        source=_source_from_availability(gold),
        profile_summary=" ".join(profile_parts) if profile_parts else None,
    )


class GoldCandidateStore:
    """Reads gold and hydrates serving Candidates (AD-091).

    At POC scale, loads the full gold pool up front. The ``get`` method
    satisfies the ``CandidateStore`` protocol.
    """

    def __init__(self, gold_dir: Path) -> None:
        self._gold_dir = gold_dir
        self._cache: dict[str, Candidate] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        ids = list_gold_ids(self._gold_dir)
        for cid in ids:
            gold = read_gold(cid, self._gold_dir)
            if gold is None:
                continue
            candidate = _hydrate(gold)
            if candidate is not None:
                self._cache[cid] = candidate
        self._loaded = True
        _log.info("candidate_store.loaded", count=len(self._cache))

    def get(self, candidate_ids: list[str]) -> list[Candidate]:
        """Return serving Candidates for the given ids."""
        self._load()
        if not candidate_ids:
            return list(self._cache.values())
        return [self._cache[cid] for cid in candidate_ids if cid in self._cache]
