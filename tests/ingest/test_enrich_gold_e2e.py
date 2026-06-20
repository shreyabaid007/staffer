"""End-to-end silver→enrich→merge→reconcile→gold (a-003 T-011).

Cassette-recorded LLM responses (no live network). Covers thin/medium/rich (PP-*/case 18), the §7
worked conflict (case 9), determinism (NF-1/case 21), a version bump (NF-2/case 22), and a
tombstone on a departed candidate (RC-1/case 15).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path

from dsm.ingest.enrich import enrich_feedback, enrich_resume
from dsm.ingest.goldstore import list_gold_ids, read_gold, write_gold
from dsm.ingest.lineage import RunMetrics, build_quality_metrics
from dsm.ingest.merge import merge_run
from dsm.ingest.models import (
    FeedbackExtraction,
    GoldCandidate,
    Grade,
    NormalizedRecord,
    NormalizedSkill,
    ProfileSummaryExtraction,
    SkillExtraction,
    SourceType,
)
from dsm.ingest.reconcile import reconcile, tombstone
from dsm.ingest.taxonomy import Taxonomy
from dsm.models import EvidenceCitation, EvidenceSource, FreeNow, Location, ProficiencyLevel

_TAXO = Taxonomy({"kotlin": "kotlin", "terraform": "terraform"})
_NO_NER = lambda _t: []  # noqa: E731 — offline NER stub for tests

_KNOWN = {"cid:thin": [], "cid:med": ["Meera Iyer"], "cid:rich": ["Rahul Verma"]}

_MED_RESUME = (
    "Meera Iyer is a MEDIUM-RESUME engineer, expert in Kotlin, built a payments platform."
)
_RICH_RESUME = (
    "Rahul Verma RICH-RESUME built IaC with Terraform across three teams. Expert in Kotlin."
)
_RICH_FEEDBACK = "Rahul has not worked with Terraform on any engagement; strong Kotlin throughout."


def _silver() -> list[NormalizedRecord]:
    def beach(cid: str, skills: list[NormalizedSkill]) -> NormalizedRecord:
        return NormalizedRecord(
            candidate_id=cid,
            source_type=SourceType.SUPPLY_BEACH,
            source_hash=f"sha256:{cid}-b",
            valid_as_of=date(2026, 6, 1),
            grade=Grade.LEAD_CONSULTANT,
            location=Location(city="Chennai"),
            availability=FreeNow(),
            skills=skills,
            extractor_version="silver-v1",
        )

    def text(cid: str, stype: SourceType, body: str, sh: str) -> NormalizedRecord:
        return NormalizedRecord(
            candidate_id=cid,
            source_type=stype,
            source_hash=sh,
            raw_text=body,
            extractor_version="silver-v1",
        )

    return [
        beach("cid:thin", [NormalizedSkill(name="kotlin")]),
        beach("cid:med", []),
        text("cid:med", SourceType.RESUME, _MED_RESUME, "sha256:med-r"),
        beach("cid:rich", []),
        text("cid:rich", SourceType.RESUME, _RICH_RESUME, "sha256:rich-r"),
        text("cid:rich", SourceType.FEEDBACK, _RICH_FEEDBACK, "sha256:rich-f"),
    ]


def _resume_cassette(text: str, _sections: list[str]) -> ProfileSummaryExtraction:
    """Fixed per-candidate resume responses (keyed off a non-PII marker in the redacted text)."""
    if "MEDIUM-RESUME" in text:
        return ProfileSummaryExtraction(
            skills=[
                SkillExtraction(
                    name="Kotlin",
                    proficiency=ProficiencyLevel.EXPERT,
                    evidence=_cite(EvidenceSource.PROFILE_PDF, "expert in Kotlin"),
                )
            ],
            projects=["payments platform"],
            domains=["payments"],
        )
    if "RICH-RESUME" in text:
        return ProfileSummaryExtraction(
            skills=[
                SkillExtraction(
                    name="Terraform",
                    evidence=_cite(
                        EvidenceSource.PROFILE_PDF, "built IaC with Terraform across three teams"
                    ),
                ),
                SkillExtraction(
                    name="Kotlin",
                    proficiency=ProficiencyLevel.EXPERT,
                    evidence=_cite(EvidenceSource.PROFILE_PDF, "Expert in Kotlin"),
                ),
            ],
            projects=["card authorization service"],
            domains=["payments"],
        )
    raise ValueError("no cassette for this resume")


def _feedback_cassette(_text: str) -> FeedbackExtraction:
    return FeedbackExtraction(
        sentiment="positive",
        confirmed_skills=["Kotlin"],
        skill_gaps=["Terraform"],
        summary="strong Kotlin, no IaC",
        evidence=_cite(EvidenceSource.FEEDBACK, "has not worked with Terraform on any engagement"),
    )


def _cite(source: EvidenceSource, text: str) -> EvidenceCitation:
    return EvidenceCitation(source=source, text=text)


def _run(silver: list[NormalizedRecord]) -> tuple[list[GoldCandidate], RunMetrics]:
    metrics = RunMetrics()
    profiles: dict[str, ProfileSummaryExtraction] = {}
    feedbacks: dict[str, list[FeedbackExtraction]] = defaultdict(list)
    for rec in silver:
        if rec.source_type is SourceType.RESUME:
            ext = enrich_resume(
                rec,
                known_pii=_KNOWN[rec.candidate_id],
                predict=_resume_cassette,
                ner=_NO_NER,
                metrics=metrics,
            )
            if ext is not None:
                profiles[rec.candidate_id] = ext
        elif rec.source_type is SourceType.FEEDBACK:
            ext = enrich_feedback(
                rec,
                known_pii=_KNOWN[rec.candidate_id],
                predict=_feedback_cassette,
                ner=_NO_NER,
                metrics=metrics,
            )
            if ext is not None:
                feedbacks[rec.candidate_id].append(ext)
    gold = merge_run(
        silver,
        profiles=profiles,
        feedbacks=dict(feedbacks),
        taxonomy=_TAXO,
        prompt_version="enrich-v1",
        model_version="anthropic/claude-sonnet-4-6",
    )
    return gold, metrics


def test_thin_medium_rich_all_valid() -> None:
    """PP-1/2/3 (case 18): every profile shape yields a valid gold entity; coverage is correct."""
    gold, metrics = _run(_silver())
    assert {g.candidate_id for g in gold} == {"cid:thin", "cid:med", "cid:rich"}
    q = build_quality_metrics(gold, run_metrics=metrics, tombstones=0)
    assert q.coverage == {"thin": 1, "medium": 1, "rich": 1}
    q.assert_clean()  # no leak blocks


def test_worked_conflict_end_to_end() -> None:
    """Case 9 (MG-5): rich profile — resume Terraform vs feedback denial → demonstrated False."""
    gold, _ = _run(_silver())
    rich = next(g for g in gold if g.candidate_id == "cid:rich")
    terraform = next(s for s in rich.skills if s.name == "terraform")
    assert terraform.demonstrated is False and terraform.conflict is not None
    sources = {c.source for c in terraform.citations}
    assert {EvidenceSource.PROFILE_PDF, EvidenceSource.FEEDBACK} <= sources
    kotlin = next(s for s in rich.skills if s.name == "kotlin")
    assert kotlin.demonstrated is True  # feedback confirmed
    assert rich.conflicts == [terraform.conflict]


def test_pipeline_is_deterministic() -> None:
    """NF-1 (case 21): identical inputs + versions → byte-identical gold."""
    a, _ = _run(_silver())
    b, _ = _run(_silver())
    assert [g.model_dump_json() for g in a] == [g.model_dump_json() for g in b]


def test_version_bump_changes_gold_hash() -> None:
    """NF-2 (case 22): a prompt_version bump changes the derivation identity (gold_hash)."""
    silver = _silver()
    v1 = merge_run(silver, taxonomy=_TAXO, prompt_version="enrich-v1", model_version="m")
    v2 = merge_run(silver, taxonomy=_TAXO, prompt_version="enrich-v2", model_version="m")
    h1 = {g.candidate_id: g.gold_hash for g in v1}
    h2 = {g.candidate_id: g.gold_hash for g in v2}
    assert h1.keys() == h2.keys()
    assert all(h1[cid] != h2[cid] for cid in h1)  # every entity re-derives


def test_tombstone_on_departed_candidate(tmp_path: Path) -> None:
    """RC-1 (case 15): a candidate present in prior gold but gone from the run is tombstoned."""
    gold, _ = _run(_silver())
    for g in gold:
        write_gold(g, tmp_path)
    # A prior entity that is no longer in the current snapshot.
    departed = next(g for g in gold if g.candidate_id == "cid:thin").model_copy(
        update={"candidate_id": "cid:gone"}
    )
    write_gold(departed, tmp_path)

    current = {g.candidate_id for g in gold}
    result = reconcile(current, prior_ids=list_gold_ids(tmp_path))
    assert result.tombstoned_ids == ["cid:gone"]
    write_gold(tombstone(departed), tmp_path)
    reloaded = read_gold("cid:gone", tmp_path)
    assert reloaded is not None and reloaded.is_tombstoned is True
