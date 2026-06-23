"""Tests for the full 9-step orchestrator + ``dsm match`` command (b-002 T-009; FR-7; §4/§10).

``run_match`` is driven with mocked seams (deterministic score predictor; store/embed_client None →
recall+rerank skipped, or a temp Milvus + FakeEmbedClient for the retrieval path). The ``match``
command runs over a tmp demand CSV + tmp gold with the live builders monkeypatched. No network.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import typer

import dsm.cli.commands as commands
from dsm.cli.commands import match, run_match
from dsm.config import load_config
from dsm.ingest.goldstore import write_gold
from dsm.ingest.models import Confidence, GoldCandidate, Grade, MergedSkill, Sourced
from dsm.match.freshness import FreshnessVerdict
from dsm.match.models import ScoreExtraction
from dsm.models import (
    Candidate,
    CandidateSource,
    FeedbackSignals,
    FlagType,
    FreeNow,
    Location,
    NoMatchResult,
    ProficiencyLevel,
    ShortlistResult,
    Skill,
    SkillDepth,
    SkillRequirement,
    TargetProfileScorecard,
)

_CONFIG = load_config()


def _predict(scorecard: TargetProfileScorecard, candidate: Candidate) -> ScoreExtraction:
    return ScoreExtraction(skill_match_score=0.8, feedback_score=0.5, narrative="ok")


def _cand(cid: str, *, city: str = "Chennai", skill: str = "kotlin") -> Candidate:
    return Candidate(
        email=cid,
        name=cid,
        location=Location(city=city),
        availability=FreeNow(),
        skills=[Skill(name=skill, proficiency=ProficiencyLevel.ADVANCED)],
        feedback=FeedbackSignals(),
        source=CandidateSource.BEACH,
    )


def _scorecard(*, hard: str = "kotlin", co_location: bool = True) -> TargetProfileScorecard:
    return TargetProfileScorecard(
        role_id="ROLE-Q1",
        hard_depth_skills=[SkillRequirement(name=hard, depth=SkillDepth.HARD)],
        desired_skills=[],
        location=Location(city="Chennai"),
        co_location_required=co_location,
        start_date=date(2026, 7, 1),
    )


class TestRunMatch:
    def test_full_pipeline_returns_shortlist_with_snapshot(self) -> None:
        result = run_match(
            [_cand("cid:a"), _cand("cid:b")],
            _scorecard(),
            score_predict=_predict,
            config=_CONFIG,
        )
        assert isinstance(result, ShortlistResult)
        assert {a.candidate.email for a in result.ranked_assessments} == {"cid:a", "cid:b"}
        # config_snapshot extended for b-002 lineage (FR-7-AC-5)
        snap = result.config_snapshot
        assert snap["top_k"] == 5
        assert "recall" in snap and "rerank" in snap
        assert snap["freshness"] is None

    def test_empty_after_gate_is_no_match(self) -> None:
        # candidate in Pune fails the Chennai onsite gate → empty pool at gate
        result = run_match(
            [_cand("cid:a", city="Pune")], _scorecard(), score_predict=_predict, config=_CONFIG
        )
        assert isinstance(result, NoMatchResult)
        assert "gate" in result.reason.lower()
        assert [nm.candidate_email for nm in result.near_misses] == ["cid:a"]

    def test_empty_after_exact_filter_is_no_match(self) -> None:
        # candidate clears the gate but lacks the hard skill → empty pool at exact filter
        result = run_match(
            [_cand("cid:a", skill="python")],
            _scorecard(hard="kotlin"),
            score_predict=_predict,
            config=_CONFIG,
        )
        assert isinstance(result, NoMatchResult)
        assert "hard-skill" in result.reason.lower()
        # AD-099: a hard-skill failure is not a near-miss (no negotiable fix), but it is still
        # recorded in the exclusion log (the transparency layer).
        assert result.near_misses == []
        assert [e.candidate_email for e in result.exclusion_log.exclusions] == ["cid:a"]

    def test_freshness_warn_flags_every_assessment(self) -> None:
        verdict = FreshnessVerdict(action="warn", staleness_days=5, message="stale-but-usable")
        result = run_match(
            [_cand("cid:a"), _cand("cid:b")],
            _scorecard(),
            score_predict=_predict,
            config=_CONFIG,
            freshness=verdict,
        )
        assert isinstance(result, ShortlistResult)
        assert result.ranked_assessments  # non-empty
        for assessment in result.ranked_assessments:
            assert any(f.type is FlagType.FRESHNESS_WARNING for f in assessment.flags)
        assert result.config_snapshot["freshness"]["action"] == "warn"


# ---------------------------------------------------------------------------
# `dsm match` command — tmp demand CSV + tmp gold, live builders monkeypatched
# ---------------------------------------------------------------------------

_CSV = (
    "Open Roles - Acme - as of 2026-06-15\n"
    "Role ID,Title,Required Skills,Start,Location,Co-location,Priority,Notes / Constraints\n"
    "ROLE-Q1,Backend Engineer,kotlin (advanced),2026-07-01,Chennai,Yes,1,\n"
)


def _gold(cid: str, *, valid_as_of: date) -> GoldCandidate:
    return GoldCandidate(
        candidate_id=cid,
        name_vault_ref=f"name:{cid}",
        email_vault_ref=f"email:{cid}",
        grade=Sourced(value=Grade.LEAD_CONSULTANT),
        location=Sourced(value=Location(city="Chennai")),
        availability=Sourced(value=FreeNow()),
        skills=[
            MergedSkill(
                name="kotlin", proficiency=ProficiencyLevel.ADVANCED, confidence=Confidence.MEDIUM
            )
        ],
        valid_as_of=valid_as_of,
        gold_hash=f"sha256:{cid}",
        merge_version="merge-v1",
        prompt_version="enrich-v1",
        model_version="anthropic/claude-sonnet-4-6",
    )


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch):
    """Monkeypatch the live builders so ``match`` runs with no LLM/Modal/Milvus."""
    monkeypatch.setattr(commands, "_build_clarify_predictor", lambda config: None)  # → echo
    monkeypatch.setattr(commands, "_build_score_predictor", lambda config: _predict)
    monkeypatch.setattr(commands, "_build_embed_client", lambda: None)  # skip recall/rerank
    monkeypatch.setattr(commands, "_build_query_store", lambda config, db_path="": None)


def _write_csv(tmp_path: Path) -> Path:
    csv_path = tmp_path / "open_roles.csv"
    csv_path.write_text(_CSV, encoding="utf-8")
    return csv_path


def test_match_command_prints_shortlist(
    tmp_path: Path, wired: None, capsys: pytest.CaptureFixture[str]
) -> None:
    gold_dir = tmp_path / "gold"
    write_gold(_gold("cid:a", valid_as_of=date(2026, 6, 10)), gold_dir)
    csv_path = _write_csv(tmp_path)

    match(role_id="ROLE-Q1", csv_path=csv_path, gold_dir=gold_dir, db_path="")

    payload = json.loads(capsys.readouterr().out)
    assert payload["role_id"] == "ROLE-Q1"
    assert [a["candidate"]["email"] for a in payload["ranked_assessments"]] == ["cid:a"]
    assert payload["config_snapshot"]["top_k"] == 5


def test_match_command_role_not_found_exits(tmp_path: Path, wired: None) -> None:
    write_gold(_gold("cid:a", valid_as_of=date(2026, 6, 10)), tmp_path / "gold")
    csv_path = _write_csv(tmp_path)
    with pytest.raises(typer.Exit) as exc:
        match(role_id="ROLE-NOPE", csv_path=csv_path, gold_dir=tmp_path / "gold", db_path="")
    assert exc.value.exit_code == 1


def test_match_command_refuses_on_stale_supply(tmp_path: Path, wired: None) -> None:
    # supply 75d older than demand → refuse → non-zero exit, no shortlist
    write_gold(_gold("cid:a", valid_as_of=date(2026, 4, 1)), tmp_path / "gold")
    csv_path = _write_csv(tmp_path)
    with pytest.raises(typer.Exit) as exc:
        match(role_id="ROLE-Q1", csv_path=csv_path, gold_dir=tmp_path / "gold", db_path="")
    assert exc.value.exit_code == 1
