"""Reconcile + tombstone tests (a-003 T-009). RC-1/RC-2/RC-3/RC-5."""

from __future__ import annotations

from datetime import date

from dsm.ingest.merge import merge_candidate
from dsm.ingest.models import (
    GoldCandidate,
    Grade,
    NormalizedRecord,
    Sourced,
    SourceType,
)
from dsm.ingest.reconcile import ReconcileResult, freshness_guard, reconcile, tombstone
from dsm.ingest.taxonomy import Taxonomy
from dsm.models import FreeNow, Location

_TAXO = Taxonomy({})
_VERSIONS = {"prompt_version": "enrich-v1", "model_version": "anthropic/claude-sonnet-4-6"}


def _gold(cid: str = "cid:1") -> GoldCandidate:
    draft = GoldCandidate(
        candidate_id=cid,
        name_vault_ref=f"name:{cid}",
        email_vault_ref=f"email:{cid}",
        availability=Sourced(value=FreeNow()),
        gold_hash="",
        merge_version="merge-v1",
        prompt_version="enrich-v1",
        model_version="m",
    )
    from dsm.ingest.merge import gold_content_hash

    return draft.model_copy(update={"gold_hash": gold_content_hash(draft)})


def test_departed_candidate_is_tombstoned() -> None:
    """RC-1: an id in the prior set but not the current set is flagged for tombstoning."""
    result = reconcile(current_ids={"cid:1", "cid:2"}, prior_ids={"cid:1", "cid:2", "cid:gone"})
    assert result.tombstoned_ids == ["cid:gone"]


def test_no_departures_when_sets_match() -> None:
    assert reconcile(current_ids={"cid:1"}, prior_ids={"cid:1"}) == ReconcileResult()


def test_new_candidate_is_not_tombstoned() -> None:
    result = reconcile(current_ids={"cid:1", "cid:new"}, prior_ids={"cid:1"})
    assert result.tombstoned_ids == []


def test_tombstone_sets_flag_and_rehashes() -> None:
    """RC-1/RC-4: tombstoning flips the flag and changes the gold_hash (re-index trigger)."""
    live = _gold()
    dead = tombstone(live)
    assert dead.is_tombstoned is True
    assert dead.gold_hash != live.gold_hash


def test_latest_snapshot_wins() -> None:
    """RC-2: across two snapshots for one candidate, the later valid_as_of is authoritative."""
    old = NormalizedRecord(
        candidate_id="cid:1",
        source_type=SourceType.SUPPLY_ROLLING_OFF,
        source_hash="sha256:old",
        valid_as_of=date(2026, 1, 1),
        grade=Grade.SENIOR_CONSULTANT,
        location=Location(city="Pune"),
        availability=FreeNow(),
        extractor_version="silver-v1",
    )
    new = NormalizedRecord(
        candidate_id="cid:1",
        source_type=SourceType.SUPPLY_BEACH,
        source_hash="sha256:new",
        valid_as_of=date(2026, 6, 1),
        grade=Grade.LEAD_CONSULTANT,
        location=Location(city="Chennai"),
        availability=FreeNow(),
        extractor_version="silver-v1",
    )
    gold = merge_candidate(
        "cid:1",
        silver=[old, new],
        profile=None,
        feedbacks=[],
        name_vault_ref="n",
        email_vault_ref="e",
        taxonomy=_TAXO,
        **_VERSIONS,
    )
    assert gold is not None
    assert (
        gold.grade is not None and gold.grade.value is Grade.LEAD_CONSULTANT
    )  # the newer snapshot
    assert gold.valid_as_of == date(2026, 6, 1)


def test_freshness_guard_warns_on_stale_snapshot() -> None:
    """RC-3/RC-5: a snapshot older than the threshold warns; today is injected."""
    warnings = freshness_guard(date(2026, 1, 1), max_staleness_days=30, today=date(2026, 6, 19))
    assert len(warnings) == 1 and "days old" in warnings[0]


def test_freshness_guard_quiet_when_fresh() -> None:
    assert freshness_guard(date(2026, 6, 10), max_staleness_days=30, today=date(2026, 6, 19)) == []
    assert freshness_guard(None, max_staleness_days=30, today=date(2026, 6, 19)) == []
