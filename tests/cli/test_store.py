"""Tests for dsm.cli.store.GoldCandidateStore (b-002 T-008; FR-1; §6.0/AD-091).

Hydration: skills exclude demonstrated-False; email/name = candidate_id; None proficiency →
BEGINNER; source derived from availability; tombstoned + thin skipped. Gold fixtures in tmp_path.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import EllipsisType

from dsm.cli.store import GoldCandidateStore
from dsm.ingest.goldstore import write_gold
from dsm.ingest.models import (
    Confidence,
    FeedbackExtraction,
    GoldCandidate,
    Grade,
    MergedSkill,
    Sourced,
)
from dsm.models import (
    AvailabilityState,
    CandidateSource,
    CandidateStore,
    NewJoiner,
    ProficiencyLevel,
    RollingOff,
)


def _gold(
    cid: str,
    *,
    skills: list[MergedSkill] | None = None,
    availability: AvailabilityState | None = None,
    location: Sourced | None | EllipsisType = ...,
    is_tombstoned: bool = False,
    feedback: list[FeedbackExtraction] | None = None,
    projects: list[str] | None = None,
) -> GoldCandidate:
    from dsm.models import FreeNow, Location

    loc = Sourced(value=Location(city="Chennai")) if location is ... else location
    return GoldCandidate(
        candidate_id=cid,
        name_vault_ref=f"name:{cid}",
        email_vault_ref=f"email:{cid}",
        grade=Sourced(value=Grade.LEAD_CONSULTANT),
        location=loc,
        availability=Sourced(value=availability or FreeNow()),
        skills=skills or [MergedSkill(name="kotlin", confidence=Confidence.MEDIUM)],
        projects=projects or [],
        feedback=feedback or [],
        is_tombstoned=is_tombstoned,
        gold_hash="sha256:g1",
        merge_version="merge-v1",
        prompt_version="enrich-v1",
        model_version="anthropic/claude-sonnet-4-6",
    )


def test_implements_candidate_store_protocol(tmp_path: Path) -> None:
    assert isinstance(GoldCandidateStore(tmp_path), CandidateStore)


def test_hydrates_pseudonymised_identity_and_skills(tmp_path: Path) -> None:
    write_gold(
        _gold(
            "cid:a",
            skills=[
                MergedSkill(name="kotlin", proficiency=ProficiencyLevel.EXPERT, demonstrated=True),
                MergedSkill(name="react", demonstrated=None),  # no proficiency → BEGINNER
                MergedSkill(name="terraform", demonstrated=False),  # denied → excluded
            ],
        ),
        tmp_path,
    )
    [cand] = GoldCandidateStore(tmp_path).get(["cid:a"])

    assert cand.email == "cid:a" and cand.name == "cid:a"  # pseudonym, no raw identity
    by_name = {s.name: s.proficiency for s in cand.skills}
    assert by_name == {"kotlin": ProficiencyLevel.EXPERT, "react": ProficiencyLevel.BEGINNER}
    assert "terraform" not in by_name  # demonstrated False excluded (mirrors AD-081)


def test_source_derived_from_availability(tmp_path: Path) -> None:
    write_gold(_gold("cid:free"), tmp_path)
    write_gold(
        _gold(
            "cid:roll", availability=RollingOff(expected_date=date(2026, 7, 1), confidence="high")
        ),
        tmp_path,
    )
    write_gold(_gold("cid:new", availability=NewJoiner(join_date=date(2026, 7, 1))), tmp_path)
    store = GoldCandidateStore(tmp_path)

    src = {c.email: c.source for c in store.get(["cid:free", "cid:roll", "cid:new"])}
    assert src == {
        "cid:free": CandidateSource.BEACH,
        "cid:roll": CandidateSource.ROLLING_OFF,
        "cid:new": CandidateSource.NEW_JOINER,
    }


def test_tombstoned_and_thin_are_skipped(tmp_path: Path) -> None:
    write_gold(_gold("cid:tomb", is_tombstoned=True), tmp_path)
    write_gold(_gold("cid:thin", location=None), tmp_path)  # no location → cannot gate
    write_gold(_gold("cid:ok"), tmp_path)
    store = GoldCandidateStore(tmp_path)

    got = {c.email for c in store.get(store.all_ids())}
    assert got == {"cid:ok"}


def test_feedback_retention_flag_and_summary(tmp_path: Path) -> None:
    fb = FeedbackExtraction(
        sentiment="very_positive",
        retention_requested=True,
        summary="Client wants to keep them on the account.",
        evidence={"source": "feedback", "text": "keep them"},  # type: ignore[arg-type]
    )
    write_gold(_gold("cid:a", feedback=[fb]), tmp_path)
    [cand] = GoldCandidateStore(tmp_path).get(["cid:a"])

    assert len(cand.feedback.entries) == 1
    entry = cand.feedback.entries[0]
    assert entry.retention_flag is True
    assert entry.sentiment == "positive"  # very_positive collapses to positive
    assert "keep them" in entry.text
