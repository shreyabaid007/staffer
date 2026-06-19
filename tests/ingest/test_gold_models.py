"""Tests for the Phase-4/5 enrich + gold models (a-003 T-004; §6)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dsm.ingest.models import (
    Confidence,
    FeedbackExtraction,
    GoldCandidate,
    Grade,
    MergedSkill,
    ProfileSummaryExtraction,
    SkillExtraction,
    Sourced,
)
from dsm.models import EvidenceCitation, EvidenceSource, FreeNow, Location, ProficiencyLevel

_CITE = EvidenceCitation(
    source=EvidenceSource.PROFILE_PDF,
    text="built card-authorization services in Kotlin",
    source_hash="sha256:abc",
    locator="resume p1",
)


def test_skill_extraction_carries_evidence() -> None:
    s = SkillExtraction(name="Kotlin", proficiency=ProficiencyLevel.EXPERT, evidence=_CITE)
    assert s.evidence.text.startswith("built card")


def test_profile_summary_defaults_empty() -> None:
    p = ProfileSummaryExtraction()
    assert p.skills == [] and p.projects == [] and p.domains == []


def test_feedback_extraction_requires_sentiment_and_summary() -> None:
    fb = FeedbackExtraction(sentiment="positive", summary="strong delivery", evidence=_CITE)
    assert fb.retention_requested is False and fb.rejection_requested is False
    with pytest.raises(ValidationError):
        FeedbackExtraction(summary="x", evidence=_CITE)  # type: ignore[call-arg]  # missing sentiment


def test_sourced_is_generic() -> None:
    g: Sourced[Grade] = Sourced(value=Grade.LEAD_CONSULTANT, citations=[_CITE])
    loc: Sourced[Location] = Sourced(value=Location(city="Chennai"))
    assert g.value is Grade.LEAD_CONSULTANT
    assert g.confidence is Confidence.MEDIUM  # default band
    assert loc.value.city == "Chennai"


def test_merged_skill_conflict_and_demonstrated() -> None:
    m = MergedSkill(
        name="terraform",
        demonstrated=False,
        confidence=Confidence.HIGH,
        citations=[_CITE, _CITE],
        conflict="resume claims terraform; feedback denies it",
    )
    assert m.demonstrated is False
    assert len(m.citations) == 2 and m.conflict is not None


def test_models_are_frozen() -> None:
    m = MergedSkill(name="kotlin", confidence=Confidence.MEDIUM)
    with pytest.raises(ValidationError):
        m.name = "java"  # type: ignore[misc]


def test_thin_gold_candidate_is_valid() -> None:
    """PP-1: a CSV-only (thin) profile yields a valid GoldCandidate — supply fields optional."""
    gold = GoldCandidate(
        candidate_id="cid:abc",
        name_vault_ref="name:cid:abc",
        email_vault_ref="email:cid:abc",
        availability=Sourced(value=FreeNow()),
        gold_hash="sha256:gold",
        merge_version="merge-v1",
        prompt_version="profile-v1",
        model_version="anthropic/claude-sonnet-4-6",
    )
    assert gold.grade is None and gold.skills == [] and gold.feedback == []
    assert gold.is_tombstoned is False


def test_rich_gold_candidate_carries_feedback_facts_no_score() -> None:
    """FB-1: gold carries the cited feedback facts; there is no score field on the entity."""
    fb = FeedbackExtraction(
        sentiment="very_positive",
        summary="kept on account",
        retention_requested=True,
        evidence=_CITE,
    )
    gold = GoldCandidate(
        candidate_id="cid:xyz",
        name_vault_ref="name:cid:xyz",
        email_vault_ref="email:cid:xyz",
        grade=Sourced(value=Grade.PRINCIPAL_CONSULTANT, citations=[_CITE]),
        availability=Sourced(value=FreeNow()),
        skills=[
            MergedSkill(
                name="kotlin",
                proficiency=ProficiencyLevel.EXPERT,
                demonstrated=True,
                confidence=Confidence.HIGH,
                citations=[_CITE],
            )
        ],
        feedback=[fb],
        gold_hash="sha256:gold",
        merge_version="merge-v1",
        prompt_version="profile-v1",
        model_version="anthropic/claude-sonnet-4-6",
    )
    assert gold.feedback[0].retention_requested is True
    assert not hasattr(
        gold, "performance_feedback_score"
    )  # score deferred to match/score (AD-079)
