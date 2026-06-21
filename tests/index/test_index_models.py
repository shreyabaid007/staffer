"""Tests for dsm.index.models — projection + IndexMetrics (a-005 T-001; IDX-1/IDX-8; AC-3)."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from dsm.index.build import build_record, is_indexable, project_filter_fields
from dsm.index.models import (
    CandidateIndexRecord,
    IndexMetrics,
    RetrievedCandidate,
)
from dsm.ingest.models import Confidence, GoldCandidate, Grade, MergedSkill, Sourced
from dsm.models import AvailabilityState, FreeNow, Location, NewJoiner, RollingOff

_DEFAULT_LOCATION = Location(city="Chennai")
_DEFAULT_AVAIL: AvailabilityState = FreeNow()
_DEFAULT_VALID_AS_OF = date(2026, 6, 1)


def _gold(
    *,
    grade: Grade | None = Grade.LEAD_CONSULTANT,
    location: Location | None = _DEFAULT_LOCATION,
    availability: AvailabilityState | None = _DEFAULT_AVAIL,
    valid_as_of: date | None = _DEFAULT_VALID_AS_OF,
    candidate_id: str = "cid:abc",
) -> GoldCandidate:
    return GoldCandidate(
        candidate_id=candidate_id,
        name_vault_ref=f"name:{candidate_id}",
        email_vault_ref=f"email:{candidate_id}",
        grade=Sourced(value=grade) if grade is not None else None,
        location=Sourced(value=location) if location is not None else None,
        availability=Sourced(value=availability) if availability is not None else None,
        skills=[MergedSkill(name="kotlin", confidence=Confidence.MEDIUM)],
        valid_as_of=valid_as_of,
        gold_hash="sha256:g1",
        merge_version="merge-v1",
        prompt_version="enrich-v1",
        model_version="anthropic/claude-sonnet-4-6",
    )


class TestIsIndexable:
    def test_complete_profile_is_indexable(self) -> None:
        assert is_indexable(_gold()) is True

    @pytest.mark.parametrize("missing", ["grade", "location", "availability"])
    def test_missing_required_field_not_indexable(self, missing: str) -> None:
        gold = _gold(**{missing: None})  # type: ignore[arg-type]
        assert is_indexable(gold) is False


class TestProjectFilterFields:
    def test_free_now_has_no_date(self) -> None:
        fields = project_filter_fields(_gold(availability=FreeNow()))
        assert fields["availability_type"] == "free_now"
        assert fields["availability_date"] is None

    def test_rolling_off_carries_expected_date(self) -> None:
        avail = RollingOff(expected_date=date(2026, 7, 15), confidence="medium")
        fields = project_filter_fields(_gold(availability=avail))
        assert fields["availability_type"] == "rolling_off"
        assert fields["availability_date"] == date(2026, 7, 15)

    def test_new_joiner_carries_join_date(self) -> None:
        fields = project_filter_fields(_gold(availability=NewJoiner(join_date=date(2026, 8, 1))))
        assert fields["availability_type"] == "new_joiner"
        assert fields["availability_date"] == date(2026, 8, 1)

    def test_maps_grade_remote_within_country_and_valid_as_of(self) -> None:
        gold = _gold(location=Location(city="Pune", remote_within_country=True))
        fields = project_filter_fields(gold)
        assert fields["grade"] is Grade.LEAD_CONSULTANT
        assert fields["city"] == "Pune"
        assert fields["remote_within_country"] is True
        assert fields["valid_as_of"] == date(2026, 6, 1)
        assert fields["gold_hash"] == "sha256:g1"

    def test_onsite_cities_projected_as_sorted_list(self) -> None:
        """AD-086: onsite_cities (a frozenset) projects to a deterministic sorted list."""
        gold = _gold(location=Location(city="Pune", onsite_cities=frozenset({"Pune", "Chennai"})))
        fields = project_filter_fields(gold)
        assert fields["onsite_cities"] == ["Chennai", "Pune"]
        assert fields["remote_within_country"] is False

    def test_remote_india_has_no_city(self) -> None:
        """AD-075: a Remote (India) consultant has city=None — projected through faithfully."""
        gold = _gold(location=Location(city=None, remote_within_country=True))
        fields = project_filter_fields(gold)
        assert fields["city"] is None
        assert fields["remote_within_country"] is True


class TestBuildRecord:
    def test_assembles_frozen_record(self) -> None:
        gold = _gold()
        record = build_record(
            gold,
            embed_text="kotlin expert.",
            dense_vector=[0.1] * 768,
            skill_set=["kotlin"],
            model_version="BAAI/bge-base-en-v1.5",
        )
        assert isinstance(record, CandidateIndexRecord)
        assert record.candidate_id == "cid:abc"
        assert record.grade is Grade.LEAD_CONSULTANT
        assert record.availability_type == "free_now"
        assert len(record.dense_vector) == 768
        assert record.model_version == "BAAI/bge-base-en-v1.5"
        assert record.gold_hash == "sha256:g1"

    def test_model_version_is_embedder_not_reasoning_llm(self) -> None:
        """AD-082: the index model_version is the embedder id, not GoldCandidate.model_version."""
        gold = _gold()
        record = build_record(
            gold,
            embed_text="x",
            dense_vector=[0.0] * 768,
            skill_set=[],
            model_version="BAAI/bge-base-en-v1.5",
        )
        assert record.model_version != gold.model_version


class TestRetrievedCandidate:
    def test_defaults_scores_none(self) -> None:
        rc = RetrievedCandidate(candidate_id="cid:abc")
        assert rc.dense_score is None
        assert rc.bm25_score is None
        assert rc.rrf_score is None
        assert rc.rerank_score is None

    def test_with_scores(self) -> None:
        rc = RetrievedCandidate(
            candidate_id="cid:abc",
            dense_score=0.85,
            bm25_score=0.72,
            rrf_score=0.78,
            rerank_score=0.91,
        )
        assert rc.dense_score == 0.85
        assert rc.rerank_score == 0.91

    def test_frozen(self) -> None:
        rc = RetrievedCandidate(candidate_id="cid:abc", dense_score=0.5)
        with pytest.raises(ValidationError):
            rc.dense_score = 0.9  # type: ignore[misc]


class TestIndexMetrics:
    def test_defaults_zero(self) -> None:
        m = IndexMetrics()
        assert (m.indexed, m.skipped_unchanged, m.tombstoned_removed, m.thin_skipped) == (
            0,
            0,
            0,
            0,
        )

    def test_assert_clean_passes_on_normal_counts(self) -> None:
        IndexMetrics(
            indexed=3, skipped_unchanged=2, tombstoned_removed=1, thin_skipped=1
        ).assert_clean()

    def test_assert_clean_raises_on_negative(self) -> None:
        with pytest.raises(RuntimeError, match="negative"):
            IndexMetrics(indexed=-1).assert_clean()
