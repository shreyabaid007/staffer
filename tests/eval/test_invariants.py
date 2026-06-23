"""Tier-1 invariant evaluators — golden cases + deliberately-failing fixtures (AD-095).

Every test is ``eval_offline``: deterministic, cassette-backed, no network, no keys.
Runs under ``make check`` via the ``eval-tier1`` target.
"""

from __future__ import annotations

from typing import Any

import pytest

from dsm.config import load_config
from dsm.eval.cases import CassetteLM, load_golden_cases, pseudonymise_candidates
from dsm.eval.invariants import (
    MatchResult,
    adjacency_flag,
    determinism,
    evidence_cited,
    gates_respected,
    hard_skill_not_cleared_by_adjacency,
    no_pii_leak,
)
from dsm.models import (
    Candidate,
    EvidenceCitation,
    EvidenceSource,
    ExclusionReason,
    Flag,
    FlagType,
    NoMatchResult,
    ShortlistResult,
    TargetProfileScorecard,
)

_CONFIG: dict[str, Any] = load_config()


def _run_pipeline(
    candidates: list[Candidate],
    scorecard: TargetProfileScorecard,
    case_id: str,
) -> MatchResult:
    """Drive ``run_match`` with cassette LM (no network)."""
    from dsm.cli.commands import run_match

    cassette = CassetteLM(case_id)
    return run_match(
        candidates,
        scorecard,
        score_predict=cassette.score_predict(),
        config=_CONFIG,
    )


# ---------------------------------------------------------------------------
# Fixtures: golden pipeline results
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def golden_cases():
    return load_golden_cases()


@pytest.fixture(scope="module")
def role_01_result(golden_cases):
    case = golden_cases[0]
    return _run_pipeline(case.candidates, case.scorecard, case.case_id)


@pytest.fixture(scope="module")
def role_02_result(golden_cases):
    case = golden_cases[1]
    return _run_pipeline(case.candidates, case.scorecard, case.case_id)


@pytest.fixture(scope="module")
def role_03_result(golden_cases):
    case = golden_cases[2]
    return _run_pipeline(case.candidates, case.scorecard, case.case_id)


# ---------------------------------------------------------------------------
# 1. gates-respected
# ---------------------------------------------------------------------------


@pytest.mark.eval_offline
class TestGatesRespected:
    def test_passes_role_01(self, role_01_result: ShortlistResult) -> None:
        r = gates_respected(role_01_result)
        assert r.passed, r.reason

    def test_passes_role_02(self, role_02_result: ShortlistResult) -> None:
        r = gates_respected(role_02_result)
        assert r.passed, r.reason

    def test_passes_role_03_no_match(self, role_03_result: NoMatchResult) -> None:
        r = gates_respected(role_03_result)
        assert r.passed, r.reason

    def test_detects_gated_candidate_in_shortlist(self, role_01_result: ShortlistResult) -> None:
        """Inject a gate-excluded candidate into ranked_assessments → must fail."""
        excluded_email = role_01_result.exclusion_log.exclusions[0].candidate_email
        fake_candidate = role_01_result.ranked_assessments[0].candidate.model_copy(
            update={"email": excluded_email}
        )
        fake_assessment = role_01_result.ranked_assessments[0].model_copy(
            update={"candidate": fake_candidate}
        )
        tampered = role_01_result.model_copy(
            update={
                "ranked_assessments": [
                    fake_assessment,
                    *role_01_result.ranked_assessments,
                ]
            }
        )
        r = gates_respected(tampered)
        assert not r.passed
        assert excluded_email in r.reason


# ---------------------------------------------------------------------------
# 2. hard-skill-not-cleared-by-adjacency
# ---------------------------------------------------------------------------


@pytest.mark.eval_offline
class TestHardSkillNotClearedByAdjacency:
    def test_passes_role_01(self, role_01_result: ShortlistResult, golden_cases) -> None:
        sc = golden_cases[0].scorecard
        r = hard_skill_not_cleared_by_adjacency(
            role_01_result,
            scorecard=sc,
            adjacency_map=_CONFIG.get("adjacency_map", {}),
        )
        assert r.passed, r.reason

    def test_detects_hard_skill_bypass(
        self, role_01_result: ShortlistResult, golden_cases
    ) -> None:
        """Move Suresh (hard-skill excluded) into ranked → must fail."""
        sc = golden_cases[0].scorecard
        suresh_exc = next(
            e
            for e in role_01_result.exclusion_log.exclusions
            if e.reason is ExclusionReason.HARD_SKILL_MISMATCH
        )
        fake_cand = role_01_result.ranked_assessments[0].candidate.model_copy(
            update={"email": suresh_exc.candidate_email}
        )
        fake_assessment = role_01_result.ranked_assessments[0].model_copy(
            update={"candidate": fake_cand}
        )
        tampered = role_01_result.model_copy(
            update={
                "ranked_assessments": [
                    fake_assessment,
                    *role_01_result.ranked_assessments,
                ]
            }
        )
        r = hard_skill_not_cleared_by_adjacency(
            tampered,
            scorecard=sc,
            adjacency_map=_CONFIG.get("adjacency_map", {}),
        )
        assert not r.passed
        assert suresh_exc.candidate_email in r.reason


# ---------------------------------------------------------------------------
# 3. evidence-cited
# ---------------------------------------------------------------------------


@pytest.mark.eval_offline
class TestEvidenceCited:
    def test_passes_role_01(self, role_01_result: ShortlistResult) -> None:
        r = evidence_cited(role_01_result)
        assert r.passed, r.reason

    def test_passes_role_02(self, role_02_result: ShortlistResult) -> None:
        r = evidence_cited(role_02_result)
        assert r.passed, r.reason

    def test_detects_fabricated_citation(self, role_01_result: ShortlistResult) -> None:
        """Inject a fabricated quote not in the candidate source → must fail."""
        fake_evidence = EvidenceCitation(
            source=EvidenceSource.FEEDBACK,
            text="This quote was never said by anyone and does not exist.",
        )
        first = role_01_result.ranked_assessments[0]
        tampered_assessment = first.model_copy(
            update={"evidence": [fake_evidence, *first.evidence]}
        )
        tampered = role_01_result.model_copy(
            update={
                "ranked_assessments": [
                    tampered_assessment,
                    *role_01_result.ranked_assessments[1:],
                ]
            }
        )
        r = evidence_cited(tampered)
        assert not r.passed
        assert "quote not in source" in r.reason


# ---------------------------------------------------------------------------
# 4. no-PII-leak
# ---------------------------------------------------------------------------


@pytest.mark.eval_offline
class TestNoPiiLeak:
    def test_passes_role_01(self, role_01_result: ShortlistResult) -> None:
        r = no_pii_leak(role_01_result)
        assert r.passed, r.reason

    def test_passes_role_03_no_match(self, role_03_result: NoMatchResult) -> None:
        r = no_pii_leak(role_03_result)
        assert r.passed, r.reason

    def test_detects_pii_in_narrative(self, role_01_result: ShortlistResult, golden_cases) -> None:
        """Inject a raw name into the narrative → must fail."""
        _, known_pii = pseudonymise_candidates(golden_cases[0].candidates)
        raw_name = known_pii[0]
        first = role_01_result.ranked_assessments[0]
        tampered_assessment = first.model_copy(
            update={"narrative": f"This candidate {raw_name} is great."}
        )
        tampered = role_01_result.model_copy(
            update={
                "ranked_assessments": [
                    tampered_assessment,
                    *role_01_result.ranked_assessments[1:],
                ]
            }
        )
        r = no_pii_leak(tampered, known_pii=known_pii)
        assert not r.passed
        assert "PII" in r.reason


# ---------------------------------------------------------------------------
# 5. determinism
# ---------------------------------------------------------------------------


@pytest.mark.eval_offline
class TestDeterminism:
    def test_role_01_deterministic(self, golden_cases) -> None:
        case = golden_cases[0]

        def _run(
            cands: list[Candidate],
            sc: TargetProfileScorecard,
            **kwargs: Any,
        ) -> ShortlistResult | NoMatchResult:
            return _run_pipeline(cands, sc, case.case_id)

        r = determinism(
            _run,
            candidates=case.candidates,
            scorecard=case.scorecard,
            run_kwargs={},
            n_trials=2,
        )
        assert r.passed, r.reason


# ---------------------------------------------------------------------------
# 6. adjacency-flag
# ---------------------------------------------------------------------------


@pytest.mark.eval_offline
class TestAdjacencyFlag:
    def test_passes_role_01(self, role_01_result: ShortlistResult, golden_cases) -> None:
        sc = golden_cases[0].scorecard
        r = adjacency_flag(
            role_01_result,
            scorecard=sc,
            adjacency_map=_CONFIG.get("adjacency_map", {}),
        )
        assert r.passed, r.reason

    def test_detects_missing_flag(self, role_01_result: ShortlistResult, golden_cases) -> None:
        """Strip ADJACENCY_USED from an assessment that earned credit → must fail."""
        sc = golden_cases[0].scorecard
        adj_map = _CONFIG.get("adjacency_map", {})
        target = None
        for a in role_01_result.ranked_assessments:
            if any(f.type is FlagType.ADJACENCY_USED for f in a.flags):
                target = a
                break
        if target is None:
            pytest.skip("No ADJACENCY_USED flag in ROLE-01 result")
        stripped = target.model_copy(
            update={"flags": [f for f in target.flags if f.type is not FlagType.ADJACENCY_USED]}
        )
        tampered = role_01_result.model_copy(
            update={
                "ranked_assessments": [
                    stripped if a is target else a for a in role_01_result.ranked_assessments
                ]
            }
        )
        r = adjacency_flag(tampered, scorecard=sc, adjacency_map=adj_map)
        assert not r.passed
        assert "ADJACENCY_USED missing" in r.reason

    def test_detects_spurious_flag(self, role_02_result: ShortlistResult, golden_cases) -> None:
        """Add ADJACENCY_USED to ROLE-02 (no desired skills) → must fail."""
        sc = golden_cases[1].scorecard
        adj_map = _CONFIG.get("adjacency_map", {})
        target = role_02_result.ranked_assessments[0]
        spurious = target.model_copy(
            update={
                "flags": [
                    *target.flags,
                    Flag(type=FlagType.ADJACENCY_USED, message="fake"),
                ]
            }
        )
        tampered = role_02_result.model_copy(
            update={
                "ranked_assessments": [
                    spurious,
                    *role_02_result.ranked_assessments[1:],
                ]
            }
        )
        r = adjacency_flag(tampered, scorecard=sc, adjacency_map=adj_map)
        assert not r.passed
        assert "ADJACENCY_USED flag present" in r.reason


# ---------------------------------------------------------------------------
# Provider guard proof
# ---------------------------------------------------------------------------


@pytest.mark.eval_offline
class TestProviderGuard:
    def test_live_provider_blocked_in_offline_eval(self) -> None:
        """Prove the conftest guard fires (R-12)."""
        from dsm.match.score import make_score_predictor

        with pytest.raises(RuntimeError, match="live provider"):
            make_score_predictor(None)  # type: ignore[arg-type]
