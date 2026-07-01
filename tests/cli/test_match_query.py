"""Tests for the NL query front door — ``dsm match --query`` (c-006 T-003; FR-1/3/4/6/7/8).

The intake predictor + parse cache + retrieval/score builders are monkeypatched so nothing live
runs (NF-1). The fake intake predictor stands in for the single LLM parse; ``assemble_role`` +
``_run_role`` are exercised for real. ``--yes`` pre-confirms (no stdin); the clarification +
decline paths drive ``typer.prompt``/``typer.confirm`` via monkeypatch.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
import typer

import dsm.cli.commands as commands
from dsm.cli.commands import match
from dsm.ingest.goldstore import write_gold
from dsm.ingest.models import Confidence, GoldCandidate, Grade, MergedSkill, Sourced
from dsm.match.models import RoleIntake, ScoreExtraction
from dsm.models import (
    Candidate,
    FreeNow,
    Location,
    ProficiencyLevel,
    ShortlistResult,
    SkillDepth,
    SkillRequirement,
    TargetProfileScorecard,
)

_TODAY = date.today()
_START = (_TODAY + timedelta(days=30)).isoformat()


def _predict(scorecard: TargetProfileScorecard, candidate: Candidate) -> ScoreExtraction:
    return ScoreExtraction(skill_match_score=0.8, feedback_score=0.5, narrative="ok")


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


class _FakeIntake:
    """A counting fake intake predictor — returns a fixed RoleIntake, records call count."""

    def __init__(self, intake: RoleIntake) -> None:
        self.intake = intake
        self.calls = 0

    def __call__(self, prose: str, today: date) -> RoleIntake:
        self.calls += 1
        return self.intake


class _DictCache:
    """An in-memory IntakeCache for the cache-hit test."""

    def __init__(self) -> None:
        self.store: dict[str, RoleIntake] = {}

    def get(self, key: str) -> RoleIntake | None:
        return self.store.get(key)

    def put(self, key: str, value: RoleIntake) -> None:
        self.store[key] = value


def _chennai_kotlin_intake() -> RoleIntake:
    return RoleIntake(
        title="Senior Kotlin Engineer",
        hard_skills=[SkillRequirement(name="kotlin", depth=SkillDepth.HARD)],
        location_city="Chennai",
        start_date_iso=_START,
        start_date_phrase="next month",
        notes="Payments domain.",
    )


@pytest.fixture
def wired_nl(monkeypatch: pytest.MonkeyPatch):
    """Patch the retrieval/score builders so the NL path runs with no LLM/Modal/Milvus."""
    monkeypatch.setattr(commands, "_build_score_predictor", lambda config: _predict)
    monkeypatch.setattr(commands, "_build_embed_client", lambda: None)
    monkeypatch.setattr(commands, "_build_query_store", lambda config, db_path="": None)
    monkeypatch.setattr(commands, "_build_clarify_predictor", lambda config: None)
    # No-match NL path builds the near-miss rationale predictor; stub it so it never hits the LLM.
    monkeypatch.setattr(
        commands, "_build_near_miss_rationale_predictor", lambda config: lambda sc, cand, gap: ""
    )


def _wire_intake(monkeypatch: pytest.MonkeyPatch, fake: _FakeIntake, cache: object) -> None:
    monkeypatch.setattr(commands, "_build_intake_predictor", lambda config: fake)
    monkeypatch.setattr(commands, "_build_intake_cache", lambda config: cache)


# ---------------------------------------------------------------------------
# FR-3 / FR-7: happy path — echo + shortlist + NL role_id + demand_as_of=today
# ---------------------------------------------------------------------------


def test_query_happy_path_echoes_and_prints_shortlist(
    tmp_path: Path,
    wired_nl: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    gold_dir = tmp_path / "gold"
    write_gold(_gold("cid:a", valid_as_of=_TODAY), gold_dir)  # fresh supply → freshness ok
    fake = _FakeIntake(_chennai_kotlin_intake())
    _wire_intake(monkeypatch, fake, _DictCache())

    match(
        query="senior kotlin engineer in chennai starting next month", gold_dir=gold_dir, yes=True
    )

    captured = capsys.readouterr()
    assert "── Parsed role ──" in captured.err  # echo goes to stderr (FR-3)
    assert "co-location    : required (onsite)" in captured.err  # Python-derived (FR-8)
    assert 'from "next month"' in captured.err  # resolved date shown WITH its phrase (FR-3-AC-1)
    assert "kotlin" in captured.err  # hard skills surfaced in the echo
    payload = json.loads(captured.out)  # stdout is pure JSON
    assert isinstance(payload["role_id"], str) and payload["role_id"].startswith(
        "NL-"
    )  # FR-7-AC-3
    assert [a["candidate"]["email"] for a in payload["ranked_assessments"]] == ["cid:a"]
    assert fake.calls == 1


# ---------------------------------------------------------------------------
# FR-4: missing location → one bounded clarification round, no LLM loop
# ---------------------------------------------------------------------------


def test_missing_location_triggers_single_clarification(
    tmp_path: Path,
    wired_nl: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    gold_dir = tmp_path / "gold"
    write_gold(_gold("cid:a", valid_as_of=_TODAY), gold_dir)
    no_location = RoleIntake(
        title="Backend Engineer",
        hard_skills=[SkillRequirement(name="kotlin", depth=SkillDepth.HARD)],
        start_date_iso=_START,
        start_date_phrase="next month",
    )
    fake = _FakeIntake(no_location)
    _wire_intake(monkeypatch, fake, _DictCache())
    monkeypatch.setattr(typer, "prompt", lambda *a, **k: "Chennai")  # operator answers once

    match(query="backend kotlin engineer starting next month", gold_dir=gold_dir, yes=True)

    payload = json.loads(capsys.readouterr().out)
    assert [a["candidate"]["email"] for a in payload["ranked_assessments"]] == ["cid:a"]
    assert fake.calls == 1  # the LLM is NOT re-invoked for the clarification (FR-4-AC-2)


def test_both_missing_fields_clarified_in_one_round(
    tmp_path: Path,
    wired_nl: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """FR-4: location AND start both missing → one round prompts for each; no LLM re-invocation."""
    gold_dir = tmp_path / "gold"
    write_gold(_gold("cid:a", valid_as_of=_TODAY), gold_dir)
    bare = RoleIntake(hard_skills=[SkillRequirement(name="kotlin", depth=SkillDepth.HARD)])
    fake = _FakeIntake(bare)
    _wire_intake(monkeypatch, fake, _DictCache())

    def _answer(text: str, **kwargs: object) -> str:
        return "Chennai" if "city" in text else _START  # location prompt vs start prompt

    monkeypatch.setattr(typer, "prompt", _answer)
    match(query="kotlin engineer", gold_dir=gold_dir, yes=True)

    payload = json.loads(capsys.readouterr().out)
    assert [a["candidate"]["email"] for a in payload["ranked_assessments"]] == ["cid:a"]
    assert fake.calls == 1  # both fields filled in one Python round, no LLM loop


def test_empty_query_aborts(
    tmp_path: Path, wired_nl: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_intake(monkeypatch, _FakeIntake(_chennai_kotlin_intake()), _DictCache())
    with pytest.raises(typer.Exit) as exc:
        match(query="   ", gold_dir=tmp_path / "gold", yes=True)
    assert exc.value.exit_code == 1  # empty prose is rejected before reaching the LLM


def test_build_intake_predictor_pins_temperature_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-1-AC-2: the live builder forwards temperature=0 to the LM (clarify/score don't)."""
    import dsm.pii.pseudonymised_lm as plm
    from dsm.config import load_config

    recorded: dict[str, object] = {}

    class _RecordingLM:
        def __init__(self, model: str, **kwargs: object) -> None:
            recorded["model"] = model
            recorded["temperature"] = kwargs.get("temperature")

    monkeypatch.setattr(plm, "PseudonymisedLM", _RecordingLM)
    config = load_config()
    commands._build_intake_predictor(config)  # builds make_intake_predictor(PseudonymisedLM(...))
    assert recorded["temperature"] == config["nl_intake"]["temperature"] == 0
    assert recorded["model"] == commands._tokenops_model(config)


def test_invalid_clarification_answer_aborts(
    tmp_path: Path, wired_nl: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    gold_dir = tmp_path / "gold"
    write_gold(_gold("cid:a", valid_as_of=_TODAY), gold_dir)
    # Missing the start date; the operator's typed answer is also invalid → abort, no second round.
    no_start = RoleIntake(
        hard_skills=[SkillRequirement(name="kotlin", depth=SkillDepth.HARD)],
        location_city="Chennai",
    )
    fake = _FakeIntake(no_start)
    _wire_intake(monkeypatch, fake, _DictCache())
    monkeypatch.setattr(typer, "prompt", lambda *a, **k: "not-a-date")

    with pytest.raises(typer.Exit) as exc:
        match(query="kotlin engineer in chennai", gold_dir=gold_dir, yes=True)
    assert exc.value.exit_code == 1
    assert fake.calls == 1  # still no LLM re-invocation (FR-4-AC-3)


# ---------------------------------------------------------------------------
# FR-3-AC-2: declining at confirm aborts with no shortlist
# ---------------------------------------------------------------------------


def test_decline_at_confirm_aborts(
    tmp_path: Path,
    wired_nl: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    gold_dir = tmp_path / "gold"
    write_gold(_gold("cid:a", valid_as_of=_TODAY), gold_dir)
    _wire_intake(monkeypatch, _FakeIntake(_chennai_kotlin_intake()), _DictCache())
    monkeypatch.setattr(typer, "confirm", lambda *a, **k: False)  # operator declines

    with pytest.raises(typer.Exit):
        match(query="kotlin engineer in chennai", gold_dir=gold_dir, yes=False)
    assert capsys.readouterr().out == ""  # no shortlist JSON printed


# ---------------------------------------------------------------------------
# FR-6-AC-1: identical query same-day hits the cache → predictor called once
# ---------------------------------------------------------------------------


def test_cache_hit_calls_predictor_once(
    tmp_path: Path, wired_nl: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    gold_dir = tmp_path / "gold"
    write_gold(_gold("cid:a", valid_as_of=_TODAY), gold_dir)
    fake = _FakeIntake(_chennai_kotlin_intake())
    _wire_intake(monkeypatch, fake, _DictCache())  # one shared cache + one shared fake

    match(query="kotlin engineer in chennai", gold_dir=gold_dir, yes=True)
    match(query="kotlin engineer in chennai", gold_dir=gold_dir, yes=True)  # same prose, same day
    assert fake.calls == 1


# ---------------------------------------------------------------------------
# FR-7-AC-4: exactly one of --query / --role-id
# ---------------------------------------------------------------------------


def test_query_negation_excludes_city_and_not_a_near_miss(
    tmp_path: Path,
    wired_nl: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """c-007: '… not Chennai' echoes the exclusion, gates out the Chennai candidate, and does not
    surface it as a near-miss (even though it clears the hard skill)."""
    gold_dir = tmp_path / "gold"
    write_gold(_gold("cid:a", valid_as_of=_TODAY), gold_dir)  # Chennai, kotlin
    intake = RoleIntake(
        title="Kotlin Engineer",
        hard_skills=[SkillRequirement(name="kotlin", depth=SkillDepth.HARD)],
        exclude_cities=["Chennai"],  # no positive city → distributed "anywhere but Chennai"
        start_date_iso=_START,
        start_date_phrase="next month",
    )
    _wire_intake(monkeypatch, _FakeIntake(intake), _DictCache())

    match(query="kotlin engineer, not chennai, starting next month", gold_dir=gold_dir, yes=True)

    captured = capsys.readouterr()
    assert "excludes" in captured.err and "chennai" in captured.err.lower()  # echoed for confirm
    payload = json.loads(captured.out)
    assert "ranked_assessments" not in payload  # no shortlist — the only candidate is excluded
    near = [nm["candidate_email"] for nm in payload.get("near_misses", [])]
    assert "cid:a" not in near  # non-negotiable exclusion → never a near-miss (FR-3-AC-5)


def test_both_flags_error(tmp_path: Path, wired_nl: None) -> None:
    with pytest.raises(typer.Exit) as exc:
        match(role_id="ROLE-1", query="kotlin in chennai", gold_dir=tmp_path / "gold")
    assert exc.value.exit_code == 1


def test_neither_flag_errors(tmp_path: Path, wired_nl: None) -> None:
    with pytest.raises(typer.Exit) as exc:
        match(gold_dir=tmp_path / "gold")
    assert exc.value.exit_code == 1


# ---------------------------------------------------------------------------
# FR-7-AC-2: NL freshness is ok/refuse only (warn structurally unreachable)
# ---------------------------------------------------------------------------


def test_stale_supply_refuses(
    tmp_path: Path, wired_nl: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    gold_dir = tmp_path / "gold"
    # demand_as_of = today; supply 100d old > reconcile.max_staleness_days (30) → refuse.
    write_gold(_gold("cid:a", valid_as_of=_TODAY - timedelta(days=100)), gold_dir)
    _wire_intake(monkeypatch, _FakeIntake(_chennai_kotlin_intake()), _DictCache())

    with pytest.raises(typer.Exit) as exc:
        match(query="kotlin engineer in chennai", gold_dir=gold_dir, yes=True)
    assert exc.value.exit_code == 1


def test_stale_but_within_bound_does_not_warn(
    tmp_path: Path,
    wired_nl: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    gold_dir = tmp_path / "gold"
    # supply 10d old (within 30); NL start >= today >= valid_as_of, so warn is unreachable → ok.
    write_gold(_gold("cid:a", valid_as_of=_TODAY - timedelta(days=10)), gold_dir)
    _wire_intake(monkeypatch, _FakeIntake(_chennai_kotlin_intake()), _DictCache())

    match(query="kotlin engineer in chennai", gold_dir=gold_dir, yes=True)

    result = ShortlistResult.model_validate_json(capsys.readouterr().out)
    flags = [f.type.value for a in result.ranked_assessments for f in a.flags]
    assert "freshness_warning" not in flags  # warn cannot fire on the NL path (FR-7-AC-2)
