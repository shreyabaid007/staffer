"""Orchestrator tests — full 9-step pipeline via run_match + CLI match() (B-002 T-011)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from dsm.cli.commands import run_match
from dsm.ingest.goldstore import write_gold
from dsm.ingest.models import (
    Confidence,
    FeedbackExtraction,
    GoldCandidate,
    MergedSkill,
    Sourced,
)
from dsm.match.freshness import WARN, FreshnessVerdict
from dsm.models import (
    Candidate,
    CandidateSource,
    EvidenceCitation,
    EvidenceSource,
    FeedbackSignals,
    FlagType,
    FreeNow,
    Grade,
    Location,
    NewJoiner,
    NoMatchResult,
    ProficiencyLevel,
    RollingOff,
    ShortlistResult,
    Skill,
    SkillDepth,
    SkillRequirement,
    TargetProfileScorecard,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _candidate(
    email: str,
    *,
    skill: str = "kotlin",
    proficiency: ProficiencyLevel = ProficiencyLevel.ADVANCED,
    city: str = "Chennai",
    availability: FreeNow | RollingOff | NewJoiner | None = None,
    source: CandidateSource = CandidateSource.BEACH,
    feedback: FeedbackSignals | None = None,
    profile_summary: str | None = None,
) -> Candidate:
    return Candidate(
        email=email,
        name=email.split("@")[0].title(),
        location=Location(city=city),
        availability=availability or FreeNow(),
        skills=[Skill(name=skill, proficiency=proficiency)],
        feedback=feedback or FeedbackSignals(),
        source=source,
        profile_summary=profile_summary,
    )


def _scorecard(
    *,
    role_id: str = "ROLE-T011",
    hard_skill: str = "kotlin",
    desired_skill: str | None = None,
    city: str = "Chennai",
    co_location: bool = False,
) -> TargetProfileScorecard:
    hard = [SkillRequirement(name=hard_skill, depth=SkillDepth.HARD)]
    desired = (
        [SkillRequirement(name=desired_skill, depth=SkillDepth.DESIRED)] if desired_skill else []
    )
    return TargetProfileScorecard(
        role_id=role_id,
        hard_depth_skills=hard,
        desired_skills=desired,
        location=Location(city=city),
        co_location_required=co_location,
        start_date=date(2026, 7, 1),
        availability_window_days=14,
    )


def _gold(
    *,
    candidate_id: str = "cid:abc",
    availability: FreeNow | RollingOff | NewJoiner | None = None,
    valid_as_of: date = date(2026, 6, 1),
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
        valid_as_of=valid_as_of,
        is_tombstoned=False,
        gold_hash="sha256:g1",
        merge_version="merge-v1",
        prompt_version="enrich-v1",
        model_version="anthropic/claude-sonnet-4-6",
    )


def _demand_csv(tmp_path: Path, *, as_of: str = "15 June 2026") -> Path:
    """Write a minimal valid demand CSV and return its path."""
    csv = tmp_path / "roles.csv"
    csv.write_text(
        f"Open Roles - TestClient - as of {as_of}\n"
        "Role ID,Title,Required Skills,Location,Co-location,Start,Priority,Notes / Constraints\n"
        "ROLE-T011,Kotlin Dev,kotlin (advanced),Chennai,No,2026-07-01,1,\n"
    )
    return csv


# ---------------------------------------------------------------------------
# run_match — full pipeline tests
# ---------------------------------------------------------------------------


class TestRunMatchPipeline:
    """Test the full 9-step pipeline via run_match with structured inputs."""

    def test_full_pipeline_stub_mode(self) -> None:
        """All steps run with no LM/embed — stub scoring, passthrough recall."""
        candidates = [
            _candidate("a@x.com"),
            _candidate("b@x.com"),
        ]
        scorecard = _scorecard()
        result = run_match(candidates, scorecard)

        assert isinstance(result, ShortlistResult)
        assert result.total_eligible == 2
        assert len(result.ranked_assessments) == 2

    def test_hard_skill_filter_excludes_candidates(self) -> None:
        """Candidates missing the hard skill are excluded after gates pass."""
        candidates = [
            _candidate("has@x.com", skill="kotlin"),
            _candidate("nope@x.com", skill="java"),
        ]
        scorecard = _scorecard(hard_skill="kotlin")
        result = run_match(candidates, scorecard)

        assert isinstance(result, ShortlistResult)
        assert result.total_eligible == 1
        emails = {a.candidate.email for a in result.ranked_assessments}
        assert emails == {"has@x.com"}
        hard_excl = [
            e for e in result.exclusion_log.exclusions if e.reason.value == "hard_skill_mismatch"
        ]
        assert len(hard_excl) == 1
        assert hard_excl[0].candidate_email == "nope@x.com"

    def test_empty_after_gates_returns_no_match(self) -> None:
        """All candidates excluded by availability gate → NoMatchResult."""
        candidates = [
            _candidate(
                "late@x.com",
                availability=RollingOff(expected_date=date(2026, 12, 1), confidence="high"),
            ),
        ]
        scorecard = _scorecard(co_location=True)
        result = run_match(candidates, scorecard)

        assert isinstance(result, NoMatchResult)
        assert "eligibility gates" in result.reason

    def test_empty_after_hard_skill_filter_returns_no_match(self) -> None:
        """All candidates pass gates but fail hard-skill filter → NoMatchResult."""
        candidates = [
            _candidate("wrong@x.com", skill="java"),
        ]
        scorecard = _scorecard(hard_skill="kotlin", co_location=False)
        result = run_match(candidates, scorecard)

        assert isinstance(result, NoMatchResult)
        assert "hard-skill" in result.reason

    def test_config_snapshot_attached(self) -> None:
        """ShortlistResult carries config_snapshot with top_k, weights, models."""
        result = run_match([_candidate("a@x.com")], _scorecard())
        assert isinstance(result, ShortlistResult)
        assert "top_k" in result.config_snapshot
        assert "weights" in result.config_snapshot
        assert "models" in result.config_snapshot

    def test_adjacency_map_wired_to_scoring(self) -> None:
        """Adjacency map is passed through to score_candidate for partial credit."""
        candidates = [
            _candidate("adj@x.com", skill="kotlin"),
        ]
        scorecard = _scorecard(hard_skill="kotlin", desired_skill="java")
        result = run_match(
            candidates,
            scorecard,
            adjacency_map={"java": ["kotlin"]},
        )
        assert isinstance(result, ShortlistResult)
        assessment = result.ranked_assessments[0]
        assert assessment.desired_skill_coverage == 0.5
        adj_flags = [f for f in assessment.flags if f.type == FlagType.ADJACENCY_USED]
        assert len(adj_flags) == 1

    def test_freshness_warn_flags_every_assessment(self) -> None:
        """A warn verdict attaches FRESHNESS_WARNING to every assessment."""
        candidates = [
            _candidate("a@x.com"),
            _candidate("b@x.com"),
        ]
        verdict = FreshnessVerdict(
            action=WARN,
            staleness_days=5,
            message="supply is 5d stale",
        )
        result = run_match(candidates, _scorecard(), freshness_verdict=verdict)

        assert isinstance(result, ShortlistResult)
        for assessment in result.ranked_assessments:
            freshness_flags = [f for f in assessment.flags if f.type == FlagType.FRESHNESS_WARNING]
            assert len(freshness_flags) == 1
            assert "5d stale" in freshness_flags[0].message

    def test_freshness_none_no_flag(self) -> None:
        """No freshness verdict → no FRESHNESS_WARNING flag."""
        result = run_match([_candidate("a@x.com")], _scorecard())
        assert isinstance(result, ShortlistResult)
        for assessment in result.ranked_assessments:
            assert all(f.type != FlagType.FRESHNESS_WARNING for f in assessment.flags)

    def test_weights_wired_to_scoring(self) -> None:
        """Custom weights alter combined_score."""
        result = run_match(
            [_candidate("a@x.com")],
            _scorecard(),
            weights={"skill": 1.0, "feedback": 0.0},
        )
        assert isinstance(result, ShortlistResult)
        assessment = result.ranked_assessments[0]
        assert assessment.combined_score == assessment.skill_match_score

    def test_exclusion_log_merges_gate_and_hard_skill_exclusions(self) -> None:
        """Exclusion log combines availability/location + hard-skill exclusions."""
        candidates = [
            _candidate("good@x.com", skill="kotlin"),
            _candidate("wrong_skill@x.com", skill="java"),
            _candidate(
                "late@x.com",
                skill="kotlin",
                availability=RollingOff(expected_date=date(2026, 12, 1), confidence="high"),
            ),
        ]
        scorecard = _scorecard(hard_skill="kotlin", co_location=False)
        result = run_match(candidates, scorecard)

        assert isinstance(result, ShortlistResult)
        reasons = {e.reason.value for e in result.exclusion_log.exclusions}
        assert "availability_mismatch" in reasons
        assert "hard_skill_mismatch" in reasons


# ---------------------------------------------------------------------------
# CLI match() — demand CSV + gold path
# ---------------------------------------------------------------------------


class TestMatchCLI:
    """Test the CLI match() function with demand CSV + gold dir."""

    def test_role_not_found_exits_1(self, tmp_path: Path) -> None:
        """Requesting a role_id not in the CSV → exit 1."""
        import typer

        from dsm.cli.commands import match

        csv = _demand_csv(tmp_path)
        gold_dir = tmp_path / "gold"
        gold_dir.mkdir()
        write_gold(_gold(candidate_id="cid:a1"), gold_dir)

        with pytest.raises(typer.Exit) as exc_info:
            match(role_id="ROLE-MISSING", demand_csv=csv, gold_dir=gold_dir)
        assert exc_info.value.exit_code == 1

    def test_empty_gold_exits_1(self, tmp_path: Path) -> None:
        """No gold candidates → exit 1."""
        import typer

        from dsm.cli.commands import match

        csv = _demand_csv(tmp_path)
        gold_dir = tmp_path / "gold"
        gold_dir.mkdir()

        with pytest.raises(typer.Exit) as exc_info:
            match(role_id="ROLE-T011", demand_csv=csv, gold_dir=gold_dir)
        assert exc_info.value.exit_code == 1

    def test_freshness_refuse_exits_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Freshness refuse verdict → exit 1."""
        import typer

        from dsm.cli.commands import match
        from dsm.match.freshness import REFUSE, FreshnessVerdict

        csv = _demand_csv(tmp_path, as_of="15 June 2026")
        gold_dir = tmp_path / "gold"
        gold_dir.mkdir()
        write_gold(_gold(candidate_id="cid:a1"), gold_dir)

        def _fake_freshness(*_args: object, **_kwargs: object) -> FreshnessVerdict:
            return FreshnessVerdict(
                action=REFUSE,
                staleness_days=60,
                message="too stale",
            )

        monkeypatch.setattr(
            "dsm.match.freshness.check_freshness",
            _fake_freshness,
        )

        with pytest.raises(typer.Exit) as exc_info:
            match(role_id="ROLE-T011", demand_csv=csv, gold_dir=gold_dir)
        assert exc_info.value.exit_code == 1

    def test_gold_dir_not_found_exits_1(self, tmp_path: Path) -> None:
        """Missing gold dir → exit 1."""
        import typer

        from dsm.cli.commands import match

        csv = _demand_csv(tmp_path)

        with pytest.raises(typer.Exit) as exc_info:
            match(role_id="ROLE-T011", demand_csv=csv, gold_dir=tmp_path / "no_such_dir")
        assert exc_info.value.exit_code == 1
