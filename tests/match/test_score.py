"""Tests for dsm.match.score (B-002 T-006; FR-5)."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

import dspy
import pytest

from dsm.match.freshness import WARN, FreshnessVerdict
from dsm.match.score import (
    _desired_skill_coverage,
    _hard_skill_coverage,
    _verify_citations,
    score_candidate,
)
from dsm.models import (
    Candidate,
    CandidateSource,
    FeedbackEntry,
    FeedbackSignals,
    FeedbackSource,
    FlagType,
    FreeNow,
    Location,
    ProficiencyLevel,
    RollingOff,
    Skill,
    SkillDepth,
    SkillRequirement,
    TargetProfileScorecard,
)

_LOC = Location(city="Pune")
_START = date(2026, 7, 1)


def _scorecard(
    *,
    hard: list[SkillRequirement] | None = None,
    desired: list[SkillRequirement] | None = None,
) -> TargetProfileScorecard:
    return TargetProfileScorecard(
        role_id="R-001",
        hard_depth_skills=hard or [],
        desired_skills=desired or [],
        location=_LOC,
        co_location_required=True,
        start_date=_START,
    )


def _candidate(
    *,
    skills: list[Skill] | None = None,
    feedback: FeedbackSignals | None = None,
    source: CandidateSource = CandidateSource.BEACH,
    availability=None,
    profile_summary: str | None = "Expert in Kotlin and React.",
) -> Candidate:
    return Candidate(
        email="test@example.com",
        name="Test User",
        location=_LOC,
        availability=availability or FreeNow(),
        skills=skills or [Skill(name="kotlin", proficiency=ProficiencyLevel.EXPERT)],
        feedback=feedback or FeedbackSignals(),
        source=source,
        profile_summary=profile_summary,
    )


class TestStubMode:
    def test_stub_returns_assessment(self) -> None:
        sc = _scorecard(
            hard=[SkillRequirement(name="kotlin", depth=SkillDepth.HARD)],
        )
        result = score_candidate(_candidate(), sc)
        assert result is not None
        assert result.skill_match_score == 1.0
        assert result.feedback_score == 0.0
        assert result.combined_score == pytest.approx(0.7)

    def test_stub_no_skills_zero_coverage(self) -> None:
        sc = _scorecard(
            hard=[SkillRequirement(name="java", depth=SkillDepth.HARD)],
        )
        result = score_candidate(_candidate(), sc)
        assert result is not None
        assert result.hard_skill_coverage == 0.0
        assert result.skill_match_score == 0.0


class TestCombineArithmetic:
    def test_default_weights(self) -> None:
        sc = _scorecard()
        result = score_candidate(
            _candidate(skills=[]),
            sc,
        )
        assert result is not None
        assert result.combined_score == pytest.approx(
            0.7 * result.skill_match_score + 0.3 * result.feedback_score
        )

    def test_custom_weights(self) -> None:
        sc = _scorecard()
        result = score_candidate(
            _candidate(skills=[]),
            sc,
            weights={"skill": 0.5, "feedback": 0.5},
        )
        assert result is not None
        assert result.combined_score == pytest.approx(
            0.5 * result.skill_match_score + 0.5 * result.feedback_score
        )


class TestHardSkillCoverage:
    def test_all_matched(self) -> None:
        cand = _candidate(
            skills=[
                Skill(name="kotlin", proficiency=ProficiencyLevel.EXPERT),
                Skill(name="java", proficiency=ProficiencyLevel.ADVANCED),
            ]
        )
        hard = [
            SkillRequirement(name="kotlin", depth=SkillDepth.HARD),
            SkillRequirement(name="java", depth=SkillDepth.HARD),
        ]
        assert _hard_skill_coverage(cand, hard) == 1.0

    def test_partial_match(self) -> None:
        cand = _candidate(
            skills=[
                Skill(name="kotlin", proficiency=ProficiencyLevel.EXPERT),
            ]
        )
        hard = [
            SkillRequirement(name="kotlin", depth=SkillDepth.HARD),
            SkillRequirement(name="java", depth=SkillDepth.HARD),
        ]
        assert _hard_skill_coverage(cand, hard) == 0.5

    def test_proficiency_floor(self) -> None:
        cand = _candidate(
            skills=[
                Skill(name="kotlin", proficiency=ProficiencyLevel.BEGINNER),
            ]
        )
        hard = [
            SkillRequirement(
                name="kotlin",
                depth=SkillDepth.HARD,
                min_proficiency=ProficiencyLevel.ADVANCED,
            ),
        ]
        assert _hard_skill_coverage(cand, hard) == 0.0

    def test_empty_hard_skills(self) -> None:
        assert _hard_skill_coverage(_candidate(), []) == 1.0


class TestDesiredSkillCoverage:
    def test_exact_match(self) -> None:
        cand = _candidate(
            skills=[
                Skill(name="react", proficiency=ProficiencyLevel.INTERMEDIATE),
            ]
        )
        desired = [SkillRequirement(name="react", depth=SkillDepth.DESIRED)]
        cov, adj = _desired_skill_coverage(cand, desired, {})
        assert cov == 1.0
        assert adj is False

    def test_adjacency_partial_credit(self) -> None:
        cand = _candidate(
            skills=[
                Skill(name="java", proficiency=ProficiencyLevel.EXPERT),
            ]
        )
        desired = [SkillRequirement(name="kotlin", depth=SkillDepth.DESIRED)]
        adj_map = {"kotlin": ["java"]}
        cov, adj = _desired_skill_coverage(cand, desired, adj_map)
        assert cov == 0.5
        assert adj is True

    def test_no_match_no_adjacency(self) -> None:
        cand = _candidate(
            skills=[
                Skill(name="python", proficiency=ProficiencyLevel.EXPERT),
            ]
        )
        desired = [SkillRequirement(name="kotlin", depth=SkillDepth.DESIRED)]
        cov, adj = _desired_skill_coverage(cand, desired, {})
        assert cov == 0.0
        assert adj is False

    def test_empty_desired(self) -> None:
        cov, adj = _desired_skill_coverage(_candidate(), [], {})
        assert cov == 1.0
        assert adj is False


class TestCitationVerification:
    def test_valid_quote_kept(self) -> None:
        source = "Expert in Kotlin and React."
        raw = json.dumps([{"source": "supply_sheet", "text": "Expert in Kotlin"}])
        result = _verify_citations(raw, source)
        assert len(result) == 1
        assert result[0].text == "Expert in Kotlin"

    def test_invalid_quote_dropped(self) -> None:
        source = "Expert in Kotlin and React."
        raw = json.dumps([{"source": "supply_sheet", "text": "Expert in Java"}])
        result = _verify_citations(raw, source)
        assert len(result) == 0

    def test_mixed_quotes(self) -> None:
        source = "Expert in Kotlin and React."
        raw = json.dumps(
            [
                {"source": "supply_sheet", "text": "Expert in Kotlin"},
                {"source": "feedback", "text": "not in source"},
            ]
        )
        result = _verify_citations(raw, source)
        assert len(result) == 1

    def test_invalid_json(self) -> None:
        assert _verify_citations("not json", "source") == []

    def test_empty_list(self) -> None:
        assert _verify_citations("[]", "source") == []


class TestFlags:
    def test_new_joiner_flag(self) -> None:
        result = score_candidate(
            _candidate(source=CandidateSource.NEW_JOINER),
            _scorecard(),
        )
        assert result is not None
        assert any(f.type == FlagType.UNVERIFIED_SKILLS for f in result.flags)

    def test_roll_off_uncertain_flag(self) -> None:
        result = score_candidate(
            _candidate(
                availability=RollingOff(
                    expected_date=date(2026, 8, 1),
                    confidence="medium",
                ),
            ),
            _scorecard(),
        )
        assert result is not None
        assert any(f.type == FlagType.ROLL_OFF_UNCERTAIN for f in result.flags)

    def test_roll_off_high_confidence_no_flag(self) -> None:
        result = score_candidate(
            _candidate(
                availability=RollingOff(
                    expected_date=date(2026, 8, 1),
                    confidence="high",
                ),
            ),
            _scorecard(),
        )
        assert result is not None
        assert not any(f.type == FlagType.ROLL_OFF_UNCERTAIN for f in result.flags)

    def test_retention_risk_flag(self) -> None:
        fb = FeedbackSignals(
            entries=[
                FeedbackEntry(
                    source=FeedbackSource.CLIENT,
                    text="Great work.",
                    retention_flag=True,
                ),
            ]
        )
        result = score_candidate(
            _candidate(feedback=fb),
            _scorecard(),
        )
        assert result is not None
        assert any(f.type == FlagType.RETENTION_RISK for f in result.flags)

    def test_adjacency_used_flag(self) -> None:
        cand = _candidate(
            skills=[
                Skill(name="java", proficiency=ProficiencyLevel.EXPERT),
            ]
        )
        sc = _scorecard(
            desired=[SkillRequirement(name="kotlin", depth=SkillDepth.DESIRED)],
        )
        result = score_candidate(cand, sc, adjacency_map={"kotlin": ["java"]})
        assert result is not None
        assert any(f.type == FlagType.ADJACENCY_USED for f in result.flags)

    def test_freshness_warning_flag(self) -> None:
        verdict = FreshnessVerdict(
            action=WARN,
            staleness_days=10,
            message="Supply snapshot is 10d stale.",
        )
        result = score_candidate(
            _candidate(),
            _scorecard(),
            freshness_verdict=verdict,
        )
        assert result is not None
        assert any(f.type == FlagType.FRESHNESS_WARNING for f in result.flags)

    def test_no_freshness_flag_when_ok(self) -> None:
        verdict = FreshnessVerdict(
            action="ok",
            staleness_days=0,
            message="Fresh.",
        )
        result = score_candidate(
            _candidate(),
            _scorecard(),
            freshness_verdict=verdict,
        )
        assert result is not None
        assert not any(f.type == FlagType.FRESHNESS_WARNING for f in result.flags)


class TestLLMPath:
    def test_llm_path_extracts_scores(self) -> None:
        mock_lm = MagicMock(spec=dspy.LM)
        mock_result = MagicMock()
        mock_result.skill_match_score = 0.9
        mock_result.feedback_score = 0.8
        mock_result.narrative = "Strong Kotlin skills."
        mock_result.evidence = json.dumps(
            [
                {"source": "supply_sheet", "text": "Expert in Kotlin"},
            ]
        )

        with patch("dsm.match.score.dspy.Predict") as mock_predict_cls:
            mock_predictor = MagicMock()
            mock_predictor.return_value = mock_result
            mock_predict_cls.return_value = mock_predictor

            result = score_candidate(
                _candidate(profile_summary="Expert in Kotlin and React."),
                _scorecard(),
                lm=mock_lm,
            )

        assert result is not None
        assert result.skill_match_score == 0.9
        assert result.feedback_score == 0.8
        assert result.combined_score == pytest.approx(0.7 * 0.9 + 0.3 * 0.8)
        assert len(result.evidence) == 1

    def test_llm_error_returns_none(self) -> None:
        mock_lm = MagicMock(spec=dspy.LM)

        with patch("dsm.match.score.dspy.Predict") as mock_predict_cls:
            mock_predictor = MagicMock()
            mock_predictor.side_effect = RuntimeError("LLM timeout")
            mock_predict_cls.return_value = mock_predictor

            result = score_candidate(
                _candidate(),
                _scorecard(),
                lm=mock_lm,
            )

        assert result is None

    def test_llm_scores_clamped(self) -> None:
        mock_lm = MagicMock(spec=dspy.LM)
        mock_result = MagicMock()
        mock_result.skill_match_score = 1.5
        mock_result.feedback_score = -0.2
        mock_result.narrative = "Out of range."
        mock_result.evidence = "[]"

        with patch("dsm.match.score.dspy.Predict") as mock_predict_cls:
            mock_predictor = MagicMock()
            mock_predictor.return_value = mock_result
            mock_predict_cls.return_value = mock_predictor

            result = score_candidate(
                _candidate(),
                _scorecard(),
                lm=mock_lm,
            )

        assert result is not None
        assert result.skill_match_score == 1.0
        assert result.feedback_score == 0.0
