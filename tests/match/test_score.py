"""Tests for dsm.match.score (b-002 T-007; FR-6; §6.8).

Sub-score extraction (mocked predict) · deterministic combine · adjacency partial credit +
ADJACENCY_USED · flags · citation verification (kept/dropped/mixed) · hard-skill-no-adjacency ·
LLM-error skip. No live network — the predictor is injected.
"""

from __future__ import annotations

from datetime import date

from dsm.match.freshness import FreshnessVerdict
from dsm.match.models import ScoreExtraction
from dsm.match.score import score_candidate
from dsm.models import (
    AvailabilityState,
    Candidate,
    CandidateSource,
    EvidenceCitation,
    EvidenceSource,
    FeedbackEntry,
    FeedbackSignals,
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

_CONFIG = {
    "weights": {"skill": 0.7, "feedback": 0.3},
    "adjacency_map": {"react": ["next.js"], "next.js": ["react"]},
}


def _candidate(
    *,
    skills: list[str] | None = None,
    source: CandidateSource = CandidateSource.BEACH,
    availability: AvailabilityState | None = None,
    feedback: FeedbackSignals | None = None,
    profile_summary: str | None = None,
) -> Candidate:
    return Candidate(
        email="cid:1",
        name="cid:1",
        location=Location(city="Chennai"),
        availability=availability or FreeNow(),
        skills=[
            Skill(name=n, proficiency=ProficiencyLevel.ADVANCED) for n in (skills or ["kotlin"])
        ],
        feedback=feedback or FeedbackSignals(),
        source=source,
        profile_summary=profile_summary,
    )


def _scorecard(
    *, hard: list[str] | None = None, desired: list[str] | None = None
) -> TargetProfileScorecard:
    return TargetProfileScorecard(
        role_id="ROLE-01",
        hard_depth_skills=[
            SkillRequirement(name=n, depth=SkillDepth.HARD) for n in (hard or ["kotlin"])
        ],
        desired_skills=[
            SkillRequirement(name=n, depth=SkillDepth.DESIRED) for n in (desired or [])
        ],
        location=Location(city="Chennai"),
        co_location_required=True,
        start_date=date(2026, 7, 1),
    )


def _predict(extraction: ScoreExtraction):
    def _p(scorecard: TargetProfileScorecard, candidate: Candidate) -> ScoreExtraction:
        return extraction

    return _p


class TestCombine:
    def test_combined_score_is_python_weighted_sum(self) -> None:
        a = score_candidate(
            _candidate(),
            _scorecard(),
            predict=_predict(ScoreExtraction(skill_match_score=0.8, feedback_score=0.6)),
            config=_CONFIG,
        )
        assert a is not None
        assert a.skill_match_score == 0.8
        assert a.feedback_score == 0.6
        assert abs(a.combined_score - (0.7 * 0.8 + 0.3 * 0.6)) < 1e-9

    def test_over_range_sub_scores_clamped_to_one(self) -> None:
        # LLM returns out-of-range highs → clamped to 1.0 before combine (AD-030); not dropped.
        a = score_candidate(
            _candidate(),
            _scorecard(),
            predict=_predict(ScoreExtraction(skill_match_score=1.4, feedback_score=2.0)),
            config=_CONFIG,
        )
        assert a is not None
        assert a.skill_match_score == 1.0
        assert a.feedback_score == 1.0
        assert 0.0 <= a.combined_score <= 1.0
        assert a.combined_score == 1.0

    def test_under_range_sub_scores_clamped_to_zero(self) -> None:
        # LLM returns negatives → clamped to 0.0 before combine (AD-030); not dropped.
        a = score_candidate(
            _candidate(),
            _scorecard(),
            predict=_predict(ScoreExtraction(skill_match_score=-0.1, feedback_score=-5.0)),
            config=_CONFIG,
        )
        assert a is not None
        assert a.skill_match_score == 0.0
        assert a.feedback_score == 0.0
        assert 0.0 <= a.combined_score <= 1.0
        assert a.combined_score == 0.0


class TestCoverage:
    def test_hard_coverage_exact_no_adjacency(self) -> None:
        # role needs kotlin + kafka; candidate holds kotlin only → 0.5 (kafka not credited)
        a = score_candidate(
            _candidate(skills=["kotlin"]),
            _scorecard(hard=["kotlin", "kafka"]),
            predict=_predict(ScoreExtraction()),
            config=_CONFIG,
        )
        assert a is not None
        assert a.hard_skill_coverage == 0.5

    def test_desired_adjacency_partial_credit_and_flag(self) -> None:
        # desired react; candidate holds next.js (adjacent) → 0.5 + ADJACENCY_USED fires
        a = score_candidate(
            _candidate(skills=["kotlin", "next.js"]),
            _scorecard(desired=["react"]),
            predict=_predict(ScoreExtraction()),
            config=_CONFIG,
        )
        assert a is not None
        assert a.desired_skill_coverage == 0.5
        assert any(f.type is FlagType.ADJACENCY_USED for f in a.flags)

    def test_desired_exact_no_adjacency_flag(self) -> None:
        a = score_candidate(
            _candidate(skills=["kotlin", "react"]),
            _scorecard(desired=["react"]),
            predict=_predict(ScoreExtraction()),
            config=_CONFIG,
        )
        assert a is not None
        assert a.desired_skill_coverage == 1.0
        assert not any(f.type is FlagType.ADJACENCY_USED for f in a.flags)


class TestFlags:
    def test_new_joiner_unverified(self) -> None:
        a = score_candidate(
            _candidate(source=CandidateSource.NEW_JOINER),
            _scorecard(),
            predict=_predict(ScoreExtraction()),
            config=_CONFIG,
        )
        assert a is not None
        assert any(f.type is FlagType.UNVERIFIED_SKILLS for f in a.flags)

    def test_rolloff_low_confidence(self) -> None:
        a = score_candidate(
            _candidate(availability=RollingOff(expected_date=date(2026, 7, 10), confidence="low")),
            _scorecard(),
            predict=_predict(ScoreExtraction()),
            config=_CONFIG,
        )
        assert a is not None
        assert any(f.type is FlagType.ROLL_OFF_UNCERTAIN for f in a.flags)

    def test_retention_risk(self) -> None:
        fb = FeedbackSignals(
            entries=[FeedbackEntry(source="client", text="keep them", retention_flag=True)]  # type: ignore[arg-type]
        )
        a = score_candidate(
            _candidate(feedback=fb),
            _scorecard(),
            predict=_predict(ScoreExtraction()),
            config=_CONFIG,
        )
        assert a is not None
        assert any(f.type is FlagType.RETENTION_RISK for f in a.flags)

    def test_freshness_warn_flag(self) -> None:
        verdict = FreshnessVerdict(action="warn", staleness_days=5, message="stale-but-usable")
        a = score_candidate(
            _candidate(),
            _scorecard(),
            predict=_predict(ScoreExtraction()),
            config=_CONFIG,
            freshness=verdict,
        )
        assert a is not None
        assert any(f.type is FlagType.FRESHNESS_WARNING for f in a.flags)

    def test_freshness_ok_no_flag(self) -> None:
        verdict = FreshnessVerdict(action="ok", staleness_days=0, message="fresh")
        a = score_candidate(
            _candidate(),
            _scorecard(),
            predict=_predict(ScoreExtraction()),
            config=_CONFIG,
            freshness=verdict,
        )
        assert a is not None
        assert not any(f.type is FlagType.FRESHNESS_WARNING for f in a.flags)


class TestCitations:
    def test_valid_quote_kept_invalid_dropped_mixed(self) -> None:
        candidate = _candidate(profile_summary="Led the payments platform rewrite.")
        extraction = ScoreExtraction(
            evidence=[
                EvidenceCitation(
                    source=EvidenceSource.PROFILE_PDF, text="Led the payments platform"
                ),
                EvidenceCitation(source=EvidenceSource.PROFILE_PDF, text="invented achievement"),
            ]
        )
        a = score_candidate(candidate, _scorecard(), predict=_predict(extraction), config=_CONFIG)
        assert a is not None
        texts = [c.text for c in a.evidence]
        assert "Led the payments platform" in texts  # verbatim-present → kept
        assert "invented achievement" not in texts  # not in source → dropped (AD-073)


class TestLLMError:
    def test_predict_error_returns_none(self) -> None:
        def _boom(scorecard: TargetProfileScorecard, candidate: Candidate) -> ScoreExtraction:
            raise RuntimeError("LM down")

        assert score_candidate(_candidate(), _scorecard(), predict=_boom, config=_CONFIG) is None
