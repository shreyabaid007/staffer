"""Merge → gold tests (a-003 T-007). §7 authority, MG-5 conflict, FB-*, PP-*."""

from __future__ import annotations

from datetime import date

from dsm.ingest.merge import gold_content_hash, merge_candidate
from dsm.ingest.models import (
    Confidence,
    FeedbackExtraction,
    Grade,
    NormalizedRecord,
    NormalizedSkill,
    ProfileSummaryExtraction,
    SkillExtraction,
    SourceType,
)
from dsm.ingest.taxonomy import Taxonomy
from dsm.models import (
    EvidenceCitation,
    EvidenceSource,
    FreeNow,
    Location,
    ProficiencyLevel,
)

_TAXO = Taxonomy({"kotlin": "kotlin", "terraform": "terraform", "iac": "terraform"})
_VERSIONS = {"prompt_version": "enrich-v1", "model_version": "anthropic/claude-sonnet-4-6"}


def _beach(
    skills: list[NormalizedSkill], *, source_hash: str = "sha256:beach"
) -> NormalizedRecord:
    return NormalizedRecord(
        candidate_id="cid:1",
        source_type=SourceType.SUPPLY_BEACH,
        source_hash=source_hash,
        valid_as_of=date(2026, 6, 1),
        grade=Grade.LEAD_CONSULTANT,
        location=Location(city="Chennai"),
        availability=FreeNow(),
        skills=skills,
        extractor_version="silver-v1",
    )


def _resume_cite(text: str) -> EvidenceCitation:
    return EvidenceCitation(source=EvidenceSource.PROFILE_PDF, text=text, source_hash="sha256:r")


def _fb_cite(text: str) -> EvidenceCitation:
    return EvidenceCitation(source=EvidenceSource.FEEDBACK, text=text, source_hash="sha256:f")


def _merge(silver, profile=None, feedbacks=None):
    return merge_candidate(
        "cid:1",
        silver=silver,
        profile=profile,
        feedbacks=feedbacks or [],
        name_vault_ref="name:cid:1",
        email_vault_ref="email:cid:1",
        taxonomy=_TAXO,
        **_VERSIONS,
    )


def test_thin_profile_yields_valid_gold() -> None:
    """PP-1: CSV-only profile is a valid GoldCandidate with supply-authority fields."""
    gold = _merge([_beach([NormalizedSkill(name="kotlin")])])
    assert gold is not None
    assert gold.grade is not None and gold.grade.value is Grade.LEAD_CONSULTANT
    assert gold.availability is not None and gold.availability.value.type == "free_now"
    assert [s.name for s in gold.skills] == ["kotlin"]
    assert gold.skills[0].demonstrated is None  # no feedback
    assert gold.feedback == []


def test_no_supply_returns_none() -> None:
    """AD-013: a record set with no supply row produces no gold entity."""
    resume_only = NormalizedRecord(
        candidate_id="cid:1",
        source_type=SourceType.RESUME,
        source_hash="sha256:r",
        raw_text="...",
        extractor_version="silver-v1",
    )
    assert _merge([resume_only]) is None


def test_proficiency_resume_over_csv() -> None:
    """MG-3: skill names union; proficiency from the resume (CSV carries none)."""
    profile = ProfileSummaryExtraction(
        skills=[
            SkillExtraction(
                name="Kotlin",
                proficiency=ProficiencyLevel.EXPERT,
                evidence=_resume_cite("expert in Kotlin"),
            )
        ]
    )
    gold = _merge([_beach([NormalizedSkill(name="kotlin")])], profile=profile)
    assert gold is not None
    kotlin = next(s for s in gold.skills if s.name == "kotlin")
    assert kotlin.proficiency is ProficiencyLevel.EXPERT


def test_demonstrated_feedback_over_resume() -> None:
    """MG-4: feedback confirmation sets demonstrated=True; a silent skill stays None."""
    profile = ProfileSummaryExtraction(
        skills=[SkillExtraction(name="Kotlin", evidence=_resume_cite("Kotlin"))]
    )
    fb = FeedbackExtraction(
        sentiment="positive",
        confirmed_skills=["kotlin"],
        summary="great with kotlin",
        evidence=_fb_cite("excellent Kotlin work"),
    )
    gold = _merge([_beach([NormalizedSkill(name="kotlin")])], profile=profile, feedbacks=[fb])
    assert gold is not None
    assert next(s for s in gold.skills if s.name == "kotlin").demonstrated is True


def test_worked_conflict_resume_vs_feedback() -> None:
    """MG-5 (case 9): resume claims Terraform, feedback denies it → demonstrated=False, both
    citations, conflict recorded on the skill and rolled up — never averaged."""
    profile = ProfileSummaryExtraction(
        skills=[
            SkillExtraction(name="Terraform", evidence=_resume_cite("built IaC with Terraform"))
        ]
    )
    fb = FeedbackExtraction(
        sentiment="neutral",
        skill_gaps=["Terraform"],
        summary="no real IaC exposure",
        evidence=_fb_cite("has not worked with Terraform"),
    )
    gold = _merge([_beach([])], profile=profile, feedbacks=[fb])
    assert gold is not None
    terraform = next(s for s in gold.skills if s.name == "terraform")
    assert terraform.demonstrated is False
    assert terraform.conflict is not None
    sources = {c.source for c in terraform.citations}
    assert EvidenceSource.PROFILE_PDF in sources and EvidenceSource.FEEDBACK in sources
    assert gold.conflicts == [terraform.conflict]  # rolled up onto the entity


def test_new_joiner_skills_stay_unverified() -> None:
    """MG-7 (AD-032): a new-joiner CV-derived skill keeps unverified through to gold."""
    nj = NormalizedRecord(
        candidate_id="cid:1",
        source_type=SourceType.SUPPLY_NEW_JOINERS,
        source_hash="sha256:nj",
        valid_as_of=date(2026, 6, 1),
        grade=Grade.SENIOR_CONSULTANT,
        location=Location(city="Pune"),
        availability=FreeNow(),
        skills=[NormalizedSkill(name="kotlin", unverified=True)],
        extractor_version="silver-v1",
    )
    gold = _merge([nj])
    assert gold is not None
    kotlin = next(s for s in gold.skills if s.name == "kotlin")
    assert kotlin.unverified is True
    assert kotlin.confidence is Confidence.LOW


def test_feedback_facts_carried_no_score() -> None:
    """FB-1/FB-2: feedback facts are carried; there is no score on the entity."""
    fb = FeedbackExtraction(
        sentiment="very_positive",
        retention_requested=True,
        summary="keep on account",
        evidence=_fb_cite("we want to keep them"),
    )
    gold = _merge([_beach([])], feedbacks=[fb])
    assert gold is not None
    assert gold.feedback[0].retention_requested is True
    assert not hasattr(gold, "performance_feedback_score")


def test_gold_hash_is_deterministic_and_excludes_itself() -> None:
    """MG-8/GS-2: identical inputs → identical gold_hash; the hash excludes the field itself."""
    a = _merge([_beach([NormalizedSkill(name="kotlin")])])
    b = _merge([_beach([NormalizedSkill(name="kotlin")])])
    assert a is not None and b is not None
    assert a.gold_hash == b.gold_hash
    assert a.gold_hash == gold_content_hash(a)  # recompute matches the stamped value
