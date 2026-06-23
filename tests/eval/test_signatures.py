"""Tier-2 signature regression — pin clarify/score DSPy signature outputs (AD-095).

Every test is ``eval_offline``: cassette-backed, no network, no keys.
Validates well-formedness of cassette responses against the typed contracts.
"""

from __future__ import annotations

import pytest

from dsm.eval.cases import CassetteLM, load_golden_cases, validate_cassette_freshness
from dsm.models import ShortlistResult


@pytest.fixture(scope="module")
def golden_cases():
    return load_golden_cases()


# ---------------------------------------------------------------------------
# Cassette freshness (prompt_hash + model_version match)
# ---------------------------------------------------------------------------


@pytest.mark.eval_offline
class TestCassetteFreshness:
    def test_role_01_cassettes_fresh(self) -> None:
        validate_cassette_freshness("ROLE-01")

    def test_role_02_cassettes_fresh(self) -> None:
        validate_cassette_freshness("ROLE-02")

    def test_role_03_cassettes_fresh(self) -> None:
        validate_cassette_freshness("ROLE-03")


# ---------------------------------------------------------------------------
# Clarify signature regression
# ---------------------------------------------------------------------------


@pytest.mark.eval_offline
class TestClarifySignature:
    def test_output_well_formed_role_01(self, golden_cases) -> None:
        """Clarify output has hard_depth_skills and desired_skills lists."""
        cassette = CassetteLM("ROLE-01")
        from dsm.models import OpenRole

        case = golden_cases[0]
        role = OpenRole(
            role_id=case.scorecard.role_id,
            title="Kotlin Developer",
            required_skills=[],
            location=case.scorecard.location,
            co_location_required=case.scorecard.co_location_required,
            start_date=case.scorecard.start_date,
        )
        predict = cassette.clarify_predict()
        result = predict(role)
        assert isinstance(result.hard_depth_skills, list)
        assert isinstance(result.desired_skills, list)
        assert len(result.hard_depth_skills) > 0


# ---------------------------------------------------------------------------
# Score signature regression
# ---------------------------------------------------------------------------


@pytest.mark.eval_offline
class TestScoreSignature:
    def test_sub_scores_in_range(self, golden_cases) -> None:
        """All cassette sub-scores are in [0, 1]."""
        from dsm.cli.commands import run_match
        from dsm.config import load_config

        config = load_config()
        case = golden_cases[0]
        cassette = CassetteLM(case.case_id)
        result = run_match(
            case.candidates,
            case.scorecard,
            score_predict=cassette.score_predict(),
            config=config,
        )
        assert isinstance(result, ShortlistResult)
        for a in result.ranked_assessments:
            assert 0.0 <= a.skill_match_score <= 1.0, (
                f"{a.candidate.email}: skill_match_score={a.skill_match_score}"
            )
            assert 0.0 <= a.feedback_score <= 1.0, (
                f"{a.candidate.email}: feedback_score={a.feedback_score}"
            )

    def test_citation_present(self, golden_cases) -> None:
        """At least one verified citation per ranked assessment."""
        from dsm.cli.commands import run_match
        from dsm.config import load_config

        config = load_config()
        case = golden_cases[0]
        cassette = CassetteLM(case.case_id)
        result = run_match(
            case.candidates,
            case.scorecard,
            score_predict=cassette.score_predict(),
            config=config,
        )
        assert isinstance(result, ShortlistResult)
        for a in result.ranked_assessments:
            assert len(a.evidence) >= 1, f"{a.candidate.email}: no verified citations"

    def test_hard_skill_no_adjacency_credit(self, golden_cases) -> None:
        """Hard skill coverage computed by exact match only (AD-033)."""
        from dsm.cli.commands import run_match
        from dsm.config import load_config

        config = load_config()
        case = golden_cases[0]
        cassette = CassetteLM(case.case_id)
        result = run_match(
            case.candidates,
            case.scorecard,
            score_predict=cassette.score_predict(),
            config=config,
        )
        assert isinstance(result, ShortlistResult)
        for a in result.ranked_assessments:
            held = {s.name for s in a.candidate.skills}
            expected = (
                1.0 if all(h.name in held for h in case.scorecard.hard_depth_skills) else 0.0
            )
            assert a.hard_skill_coverage == expected, (
                f"{a.candidate.email}: hard_skill_coverage "
                f"{a.hard_skill_coverage} != expected {expected}"
            )
