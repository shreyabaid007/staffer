"""Tests for `dsm explain` lineage dump (b-002 T-010; FR-8; §9).

Drives the ``explain`` command over a tmp demand CSV + tmp gold with live builders monkeypatched
(no LLM/Modal/Milvus). Asserts the lineage structure for both the shortlist and no-match outcomes.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import dsm.cli.commands as commands
from dsm.cli.commands import explain
from dsm.ingest.goldstore import write_gold
from dsm.ingest.models import Confidence, GoldCandidate, Grade, MergedSkill, Sourced
from dsm.match.models import ScoreExtraction
from dsm.models import Candidate, FreeNow, Location, ProficiencyLevel, TargetProfileScorecard

_CSV = (
    "Open Roles - Acme - as of 2026-06-15\n"
    "Role ID,Title,Required Skills,Start,Location,Co-location,Priority,Notes / Constraints\n"
    "ROLE-Q1,Backend Engineer,kotlin (advanced),2026-07-01,Chennai,Yes,1,\n"
)


def _predict(scorecard: TargetProfileScorecard, candidate: Candidate) -> ScoreExtraction:
    return ScoreExtraction(skill_match_score=0.8, feedback_score=0.5, narrative="strong kotlin")


def _gold(cid: str, *, city: str = "Chennai") -> GoldCandidate:
    return GoldCandidate(
        candidate_id=cid,
        name_vault_ref=f"name:{cid}",
        email_vault_ref=f"email:{cid}",
        grade=Sourced(value=Grade.LEAD_CONSULTANT),
        location=Sourced(value=Location(city=city)),
        availability=Sourced(value=FreeNow()),
        skills=[
            MergedSkill(
                name="kotlin", proficiency=ProficiencyLevel.ADVANCED, confidence=Confidence.MEDIUM
            )
        ],
        valid_as_of=date(2026, 6, 10),
        gold_hash=f"sha256:{cid}",
        merge_version="merge-v1",
        prompt_version="enrich-v1",
        model_version="anthropic/claude-sonnet-4-6",
    )


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(commands, "_build_clarify_predictor", lambda config: None)
    monkeypatch.setattr(commands, "_build_score_predictor", lambda config: _predict)
    monkeypatch.setattr(commands, "_build_embed_client", lambda: None)
    monkeypatch.setattr(commands, "_build_query_store", lambda config, db_path="": None)


def _csv(tmp_path: Path) -> Path:
    p = tmp_path / "open_roles.csv"
    p.write_text(_CSV, encoding="utf-8")
    return p


def test_explain_shortlist_lineage(
    tmp_path: Path, wired: None, capsys: pytest.CaptureFixture[str]
) -> None:
    write_gold(_gold("cid:a"), tmp_path / "gold")
    explain(role_id="ROLE-Q1", csv_path=_csv(tmp_path), gold_dir=tmp_path / "gold", db_path="")

    lineage = json.loads(capsys.readouterr().out)
    assert lineage["outcome"] == "shortlist"
    assert lineage["role_id"] == "ROLE-Q1"
    assert lineage["recall_mode"] == "hybrid"  # recall ON by default (config enabled: true)
    assert lineage["total_eligible"] == 1
    assert "config_snapshot" in lineage
    [line] = lineage["shortlist"]
    assert line["candidate"] == "cid:a"
    assert set(line) >= {
        "combined_score",
        "skill_match_score",
        "feedback_score",
        "hard_skill_coverage",
        "desired_skill_coverage",
        "flags",
        "evidence",
        "narrative",
    }


def test_explain_no_match_lineage(
    tmp_path: Path, wired: None, capsys: pytest.CaptureFixture[str]
) -> None:
    # candidate in Pune fails the Chennai onsite gate → no-match lineage
    write_gold(_gold("cid:a", city="Pune"), tmp_path / "gold")
    explain(role_id="ROLE-Q1", csv_path=_csv(tmp_path), gold_dir=tmp_path / "gold", db_path="")

    lineage = json.loads(capsys.readouterr().out)
    assert lineage["outcome"] == "no_match"
    assert lineage["reason"]
    assert lineage["exclusions"][0]["candidate"] == "cid:a"
    assert lineage["near_misses"][0]["candidate"] == "cid:a"
