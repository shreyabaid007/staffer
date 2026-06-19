"""Enrich pipeline tests — cassette-recorded, no live LLM (a-003 T-006). EN-1..7, PII-5."""

from __future__ import annotations

from dsm.ingest.enrich import enrich_feedback, enrich_resume
from dsm.ingest.models import (
    FeedbackExtraction,
    NormalizedRecord,
    ProfileSummaryExtraction,
    SkillExtraction,
    SourceType,
)
from dsm.models import EvidenceCitation, EvidenceSource, ProficiencyLevel

_RESUME_TEXT = (
    "Aarav Sharma built card-authorization services in Kotlin at Meridian Pay. "
    "Led delivery for a payments platform."
)
_KNOWN = ["Aarav Sharma", "aarav.sharma@ee.com"]


def _resume_record() -> NormalizedRecord:
    return NormalizedRecord(
        candidate_id="cid:1",
        source_type=SourceType.RESUME,
        source_hash="sha256:resume",
        raw_text=_RESUME_TEXT,
        extractor_version="silver-v1",
    )


def _cite(text: str) -> EvidenceCitation:
    return EvidenceCitation(
        source=EvidenceSource.PROFILE_PDF, text=text, source_hash="sha256:resume"
    )


def test_resume_extraction_keeps_verified_skill() -> None:
    """EN-1/EN-5: a skill whose quote is present in the source is kept, cited."""
    cassette = ProfileSummaryExtraction(
        skills=[
            SkillExtraction(
                name="Kotlin",
                proficiency=ProficiencyLevel.EXPERT,
                evidence=_cite("built card-authorization services in Kotlin"),
            )
        ],
        projects=["payments platform"],
    )
    out = enrich_resume(
        _resume_record(), known_pii=_KNOWN, predict=lambda _t, _s: cassette, ner=lambda _t: []
    )
    assert out is not None
    assert [s.name for s in out.skills] == ["Kotlin"]


def test_llm_only_sees_redacted_text() -> None:
    """EN-3/PII-5: the text handed to the LLM contains no known PII."""
    seen: dict[str, str] = {}

    def capture(text: str, _sections: list[str]) -> ProfileSummaryExtraction:
        seen["text"] = text
        return ProfileSummaryExtraction()

    enrich_resume(_resume_record(), known_pii=_KNOWN, predict=capture, ner=lambda _t: [])
    assert "Aarav Sharma" not in seen["text"]
    assert "[[PII_" in seen["text"]  # a known-PII placeholder replaced the name


def test_citation_with_placeholder_is_deanonymized_then_verified() -> None:
    """EN-4: a quote returned over redacted text de-anonymizes and verifies against source."""
    # known_pii is the name only here so the placeholder index is deterministic ([[PII_0]]).
    cassette = ProfileSummaryExtraction(
        skills=[
            SkillExtraction(
                name="Kotlin",
                evidence=_cite("[[PII_0]] built card-authorization services in Kotlin"),
            )
        ],
    )
    out = enrich_resume(
        _resume_record(),
        known_pii=["Aarav Sharma"],
        predict=lambda _t, _s: cassette,
        ner=lambda _t: [],
    )
    assert out is not None and len(out.skills) == 1
    assert out.skills[0].evidence.text.startswith("Aarav Sharma built")  # de-anonymized


def test_fabricated_quote_is_rejected_siblings_kept() -> None:
    """EN-4 (case 7): a skill whose quote is absent is dropped; a valid sibling survives."""
    cassette = ProfileSummaryExtraction(
        skills=[
            SkillExtraction(
                name="Kotlin", evidence=_cite("built card-authorization services in Kotlin")
            ),
            SkillExtraction(name="Terraform", evidence=_cite("ten years of hands-on Terraform")),
        ],
    )
    out = enrich_resume(
        _resume_record(), known_pii=_KNOWN, predict=lambda _t, _s: cassette, ner=lambda _t: []
    )
    assert out is not None
    assert [s.name for s in out.skills] == ["Kotlin"]  # fabricated Terraform dropped


def test_schema_invalid_output_returns_none() -> None:
    """EN-7: a schema-invalid / erroring LLM response yields None, not a crash."""

    def boom(_t: str, _s: list[str]) -> ProfileSummaryExtraction:
        raise ValueError("unparseable LLM output")

    assert (
        enrich_resume(_resume_record(), known_pii=_KNOWN, predict=boom, ner=lambda _t: []) is None
    )


def test_empty_raw_text_returns_none() -> None:
    rec = _resume_record().model_copy(update={"raw_text": None})
    assert (
        enrich_resume(
            rec,
            known_pii=_KNOWN,
            predict=lambda _t, _s: ProfileSummaryExtraction(),
            ner=lambda _t: [],
        )
        is None
    )


def test_feedback_extraction_verified() -> None:
    """EN-2: a feedback item with a present quote is kept with its signals."""
    rec = NormalizedRecord(
        candidate_id="cid:1",
        source_type=SourceType.FEEDBACK,
        source_hash="sha256:fb",
        raw_text="Strong engineer but has not worked with Terraform on any engagement.",
        extractor_version="silver-v1",
    )
    cassette = FeedbackExtraction(
        sentiment="positive",
        skill_gaps=["Terraform"],
        summary="solid, lacks IaC",
        evidence=EvidenceCitation(
            source=EvidenceSource.FEEDBACK, text="has not worked with Terraform on any engagement"
        ),
    )
    out = enrich_feedback(rec, known_pii=_KNOWN, predict=lambda _t: cassette, ner=lambda _t: [])
    assert out is not None and out.skill_gaps == ["Terraform"]


def test_feedback_fabricated_quote_rejected() -> None:
    """EN-4: a feedback item whose single quote is absent is rejected entirely."""
    rec = NormalizedRecord(
        candidate_id="cid:1",
        source_type=SourceType.FEEDBACK,
        source_hash="sha256:fb",
        raw_text="Strong engineer, great with Kotlin.",
        extractor_version="silver-v1",
    )
    cassette = FeedbackExtraction(
        sentiment="positive",
        summary="x",
        evidence=EvidenceCitation(source=EvidenceSource.FEEDBACK, text="quote not in the source"),
    )
    assert (
        enrich_feedback(rec, known_pii=_KNOWN, predict=lambda _t: cassette, ner=lambda _t: [])
        is None
    )
