"""Merge — silver + enriched → one canonical ``GoldCandidate`` per ``candidate_id`` (§5/5, §7).

Provenance-weighted merge, never a blind union or average (§7 authority table):

- grade / location / availability ← the **latest supply snapshot** (operational system of record).
- skill *names* ← union of all sources (widest recall); *proficiency* ← resume > CSV.
- skill *truth* (``demonstrated``) ← **feedback > resume**: confirmed → True, denied → False,
  silent → None (unverified). A resume claim that feedback denies is a **conflict**:
  ``demonstrated=False``, both citations attached, the disagreement recorded on the skill and
  rolled up onto the entity — **never averaged** (MG-5).
- domains ← resume claims; feedback confirmation raises confidence (not overwrite). projects ←
  resume.
- feedback ← the cited per-item facts are carried; the feedback **score** is a match-time concern,
  not computed here (FB-2/AD-079).

Deterministic: inputs are sorted; the LLM responses are fixed (cassettes/temp 0). The result is
stamped with ``merge_version`` + the ``prompt_version``/``model_version`` used to enrich (§11).
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from dsm.ingest.models import (
    Confidence,
    FeedbackExtraction,
    GoldCandidate,
    Grade,
    MergedSkill,
    NormalizedRecord,
    ProfileSummaryExtraction,
    Sourced,
    SourceType,
)
from dsm.ingest.taxonomy import Taxonomy, load_taxonomy
from dsm.models import (
    AvailabilityState,
    EvidenceCitation,
    EvidenceSource,
    Location,
    ProficiencyLevel,
)

MERGE_VERSION = "merge-v1"

_SUPPLY_TYPES = {
    SourceType.SUPPLY_BEACH,
    SourceType.SUPPLY_ROLLING_OFF,
    SourceType.SUPPLY_NEW_JOINERS,
}


def gold_content_hash(candidate: GoldCandidate) -> str:
    """Stable content hash over a gold entity, **excluding** ``gold_hash`` itself (GS-2).

    Canonical (sorted keys, json mode) so the same content always hashes identically — the index
    phase compares this to re-embed only changed entities. Lives here (the producer); ``goldstore``
    re-exports it for change-detection consumers.
    """
    payload = candidate.model_dump(exclude={"gold_hash"}, mode="json")
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class _SkillAcc:
    """Mutable per-skill accumulator during the merge (collapsed to ``MergedSkill`` at the end)."""

    proficiency: ProficiencyLevel | None = None
    demonstrated: bool | None = None
    unverified: bool = False
    resume_claimed: bool = False
    denied: bool = False
    citations: list[EvidenceCitation] = field(default_factory=list)


def _supply_cite(record: NormalizedRecord, text: str) -> EvidenceCitation:
    return EvidenceCitation(
        source=EvidenceSource.SUPPLY_SHEET,
        text=text,
        source_hash=record.source_hash,
        locator=f"supply {record.source_type.value}",
    )


def _latest_supply(records: list[NormalizedRecord]) -> NormalizedRecord:
    """The authoritative supply record: latest ``valid_as_of`` (ties broken by source_hash)."""
    return max(records, key=lambda r: ((r.valid_as_of or date.min), r.source_hash))


def _skill_confidence(acc: _SkillAcc) -> Confidence:
    if acc.demonstrated is True or acc.demonstrated is False:
        return (
            Confidence.HIGH
        )  # feedback spoke (confirmed or denied) — high confidence in the truth
    if acc.unverified:
        return Confidence.LOW  # new-joiner CV-derived, no corroboration (AD-032)
    return Confidence.MEDIUM


def _merge_skills(
    supply: list[NormalizedRecord],
    profile: ProfileSummaryExtraction | None,
    feedbacks: list[FeedbackExtraction],
    taxonomy: Taxonomy,
) -> list[MergedSkill]:
    acc: dict[str, _SkillAcc] = defaultdict(_SkillAcc)

    # Supply skills (already taxonomy-canonical from silver); carry the new-joiner unverified flag.
    for record in supply:
        for skill in record.skills:
            entry = acc[skill.name]
            entry.unverified = entry.unverified or skill.unverified
            entry.citations.append(_supply_cite(record, skill.name))

    # Resume skills: proficiency resume>CSV; mark resume-claimed for conflict detection.
    if profile is not None:
        for se in profile.skills:
            name, _ = taxonomy.canonical_skill(se.name)
            entry = acc[name]
            entry.resume_claimed = True
            if se.proficiency is not None:
                entry.proficiency = se.proficiency
            entry.citations.append(se.evidence)

    # Feedback: confirmed → demonstrated True; gaps → denied (False). feedback > resume.
    for fb in feedbacks:
        for cs in fb.confirmed_skills:
            name, _ = taxonomy.canonical_skill(cs)
            entry = acc[name]
            if not entry.denied:
                entry.demonstrated = True
            entry.citations.append(fb.evidence)
        for gap in fb.skill_gaps:
            name, _ = taxonomy.canonical_skill(gap)
            entry = acc[name]
            entry.denied = True
            entry.demonstrated = False  # denial wins for truth (cautious)
            entry.citations.append(fb.evidence)

    merged: list[MergedSkill] = []
    for name in sorted(acc):
        entry = acc[name]
        conflict = None
        if entry.denied and (entry.resume_claimed or entry.demonstrated is True):
            conflict = f"resume claims {name}; feedback denies it"
        merged.append(
            MergedSkill(
                name=name,
                proficiency=entry.proficiency,
                demonstrated=entry.demonstrated,
                unverified=entry.unverified,
                confidence=_skill_confidence(entry),
                citations=entry.citations,
                conflict=conflict,
            )
        )
    return merged


def _merge_domains(
    profile: ProfileSummaryExtraction | None, feedbacks: list[FeedbackExtraction]
) -> list[Sourced[str]]:
    if profile is None:
        return []
    confirmed = {
        fb.domain_confirmation.strip().lower(): fb.evidence
        for fb in feedbacks
        if fb.domain_confirmation
    }
    out: list[Sourced[str]] = []
    for domain in sorted(set(profile.domains)):
        cite = confirmed.get(domain.strip().lower())
        if cite is not None:
            out.append(Sourced(value=domain, confidence=Confidence.HIGH, citations=[cite]))
        else:
            out.append(Sourced(value=domain, confidence=Confidence.MEDIUM))
    return out


def merge_candidate(
    candidate_id: str,
    *,
    silver: list[NormalizedRecord],
    profile: ProfileSummaryExtraction | None,
    feedbacks: list[FeedbackExtraction],
    name_vault_ref: str,
    email_vault_ref: str,
    taxonomy: Taxonomy,
    merge_version: str = MERGE_VERSION,
    prompt_version: str,
    model_version: str,
) -> GoldCandidate | None:
    """Merge all records for one ``candidate_id`` into a canonical ``GoldCandidate`` (None if no
    supply state — the candidate universe is the supply sheets, AD-013)."""
    supply = [r for r in silver if r.source_type in _SUPPLY_TYPES]
    if not supply:
        return None
    latest = _latest_supply(supply)

    grade = (
        Sourced[Grade](
            value=latest.grade,
            citations=[_supply_cite(latest, latest.grade.value)],
            confidence=Confidence.HIGH,
        )
        if latest.grade is not None
        else None
    )
    location = (
        Sourced[Location](
            value=latest.location,
            citations=[_supply_cite(latest, "location")],
            confidence=Confidence.HIGH,
        )
        if latest.location is not None
        else None
    )
    availability = (
        Sourced[AvailabilityState](
            value=latest.availability,
            citations=[_supply_cite(latest, latest.availability.type)],
            confidence=Confidence.HIGH,
        )
        if latest.availability is not None
        else None
    )

    skills = _merge_skills(supply, profile, feedbacks, taxonomy)
    domains = _merge_domains(profile, feedbacks)
    projects = list(profile.projects) if profile is not None else []
    feedback_sorted = sorted(feedbacks, key=lambda f: (f.summary, f.evidence.text))
    valid_as_of = max((r.valid_as_of for r in supply if r.valid_as_of), default=None)
    conflicts = sorted({s.conflict for s in skills if s.conflict})

    draft = GoldCandidate(
        candidate_id=candidate_id,
        name_vault_ref=name_vault_ref,
        email_vault_ref=email_vault_ref,
        grade=grade,
        location=location,
        availability=availability,
        skills=skills,
        domains=domains,
        projects=projects,
        feedback=feedback_sorted,
        valid_as_of=valid_as_of,
        conflicts=conflicts,
        gold_hash="",
        merge_version=merge_version,
        prompt_version=prompt_version,
        model_version=model_version,
    )
    return draft.model_copy(update={"gold_hash": gold_content_hash(draft)})


def merge_run(
    silver: list[NormalizedRecord],
    *,
    profiles: dict[str, ProfileSummaryExtraction] | None = None,
    feedbacks: dict[str, list[FeedbackExtraction]] | None = None,
    identities: dict[str, tuple[str, str]] | None = None,
    taxonomy: Taxonomy | None = None,
    merge_version: str = MERGE_VERSION,
    prompt_version: str,
    model_version: str,
) -> list[GoldCandidate]:
    """Group all silver by ``candidate_id`` and merge each into one ``GoldCandidate`` (sorted)."""
    profiles = profiles or {}
    feedbacks = feedbacks or {}
    identities = identities or {}
    taxonomy = taxonomy or load_taxonomy()

    by_candidate: dict[str, list[NormalizedRecord]] = defaultdict(list)
    for record in silver:
        by_candidate[record.candidate_id].append(record)

    out: list[GoldCandidate] = []
    for candidate_id in sorted(by_candidate):
        name_ref, email_ref = identities.get(
            candidate_id, (f"name:{candidate_id}", f"email:{candidate_id}")
        )
        gold = merge_candidate(
            candidate_id,
            silver=by_candidate[candidate_id],
            profile=profiles.get(candidate_id),
            feedbacks=feedbacks.get(candidate_id, []),
            name_vault_ref=name_ref,
            email_vault_ref=email_ref,
            taxonomy=taxonomy,
            merge_version=merge_version,
            prompt_version=prompt_version,
            model_version=model_version,
        )
        if gold is not None:
            out.append(gold)
    return out
