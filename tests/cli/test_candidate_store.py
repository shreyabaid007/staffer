"""Tests for GoldCandidateStore adapter (B-002 T-009; FR-2; AD-091)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from dsm.cli.candidate_store import GoldCandidateStore, _hydrate
from dsm.ingest.goldstore import write_gold
from dsm.ingest.models import (
    Confidence,
    FeedbackExtraction,
    GoldCandidate,
    MergedSkill,
    Sourced,
)
from dsm.models import (
    CandidateSource,
    EvidenceCitation,
    EvidenceSource,
    FreeNow,
    Grade,
    Location,
    NewJoiner,
    ProficiencyLevel,
    RollingOff,
)


def _gold(
    *,
    candidate_id: str = "cid:abc",
    availability=None,
    tombstoned: bool = False,
) -> GoldCandidate:
    return GoldCandidate(
        candidate_id=candidate_id,
        name_vault_ref=f"name:{candidate_id}",
        email_vault_ref=f"email:{candidate_id}",
        grade=Sourced(value=Grade.LEAD_CONSULTANT),
        location=Sourced(value=Location(city="Chennai")),
        availability=Sourced(value=availability or FreeNow()),
        skills=[MergedSkill(name="kotlin", confidence=Confidence.MEDIUM)],
        domains=[Sourced(value="payments")],
        projects=["Built settlement pipeline."],
        feedback=[
            FeedbackExtraction(
                sentiment="positive",
                summary="Great team player.",
                evidence=EvidenceCitation(
                    source=EvidenceSource.FEEDBACK,
                    text="Great team player.",
                ),
            ),
        ],
        valid_as_of=date(2026, 6, 1),
        is_tombstoned=tombstoned,
        gold_hash="sha256:g1",
        merge_version="merge-v1",
        prompt_version="enrich-v1",
        model_version="anthropic/claude-sonnet-4-6",
    )


class TestHydrate:
    def test_hydrates_basic_candidate(self) -> None:
        gold = _gold()
        candidate = _hydrate(gold)
        assert candidate is not None
        assert candidate.email == "email:cid:abc"
        assert candidate.name == "name:cid:abc"
        assert candidate.location.city == "Chennai"
        assert len(candidate.skills) == 1
        assert candidate.skills[0].name == "kotlin"
        assert candidate.skills[0].proficiency == ProficiencyLevel.INTERMEDIATE

    def test_tombstoned_returns_none(self) -> None:
        assert _hydrate(_gold(tombstoned=True)) is None

    def test_missing_location_returns_none(self) -> None:
        gold = _gold()
        gold_no_loc = gold.model_copy(update={"location": None})
        assert _hydrate(gold_no_loc) is None

    def test_new_joiner_source(self) -> None:
        gold = _gold(availability=NewJoiner(join_date=date(2026, 8, 1)))
        candidate = _hydrate(gold)
        assert candidate is not None
        assert candidate.source == CandidateSource.NEW_JOINER

    def test_rolling_off_source(self) -> None:
        gold = _gold(
            availability=RollingOff(expected_date=date(2026, 8, 1), confidence="high"),
        )
        candidate = _hydrate(gold)
        assert candidate is not None
        assert candidate.source == CandidateSource.ROLLING_OFF

    def test_beach_source(self) -> None:
        gold = _gold(availability=FreeNow())
        candidate = _hydrate(gold)
        assert candidate is not None
        assert candidate.source == CandidateSource.BEACH

    def test_feedback_hydrated(self) -> None:
        gold = _gold()
        candidate = _hydrate(gold)
        assert candidate is not None
        assert len(candidate.feedback.entries) == 1
        assert candidate.feedback.entries[0].text == "Great team player."

    def test_profile_summary_from_domains_and_projects(self) -> None:
        gold = _gold()
        candidate = _hydrate(gold)
        assert candidate is not None
        assert candidate.profile_summary is not None
        assert "payments" in candidate.profile_summary
        assert "settlement" in candidate.profile_summary


class TestGoldCandidateStore:
    def test_loads_from_disk(self, tmp_path: Path) -> None:
        gold_dir = tmp_path / "gold"
        gold_dir.mkdir()
        write_gold(_gold(candidate_id="cid:a1"), gold_dir)
        write_gold(_gold(candidate_id="cid:a2"), gold_dir)

        store = GoldCandidateStore(gold_dir)
        all_candidates = store.get([])
        assert len(all_candidates) == 2

    def test_get_specific_ids(self, tmp_path: Path) -> None:
        gold_dir = tmp_path / "gold"
        gold_dir.mkdir()
        write_gold(_gold(candidate_id="cid:a1"), gold_dir)
        write_gold(_gold(candidate_id="cid:a2"), gold_dir)

        store = GoldCandidateStore(gold_dir)
        result = store.get(["cid:a1"])
        assert len(result) == 1

    def test_empty_dir(self, tmp_path: Path) -> None:
        gold_dir = tmp_path / "gold"
        gold_dir.mkdir()
        store = GoldCandidateStore(gold_dir)
        assert store.get([]) == []

    def test_tombstoned_excluded(self, tmp_path: Path) -> None:
        gold_dir = tmp_path / "gold"
        gold_dir.mkdir()
        write_gold(_gold(candidate_id="cid:alive"), gold_dir)
        write_gold(_gold(candidate_id="cid:dead", tombstoned=True), gold_dir)

        store = GoldCandidateStore(gold_dir)
        all_candidates = store.get([])
        assert len(all_candidates) == 1
