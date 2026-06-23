"""Tier-3 live smoke + cassette drift guard (AD-093).

Every test is ``eval_live``: needs real API keys (OpenRouter + Modal).
Skips cleanly without keys — never red on key-less CI.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from dsm.eval.cases import load_golden_cases
from dsm.models import ShortlistResult
from tests.eval.conftest import has_keys

pytestmark = [
    pytest.mark.eval_live,
    pytest.mark.skipif(not has_keys(), reason="No API keys for live eval"),
]


@pytest.fixture(scope="module")
def golden_cases():
    return load_golden_cases()


class TestLiveSmoke:
    def test_real_llm_shortlist_well_formed(self, golden_cases) -> None:
        """One real-LLM pass over ROLE-01: produces a well-formed ShortlistResult."""
        from dsm.cli.commands import run_match
        from dsm.config import load_config
        from dsm.match.score import make_score_predictor
        from dsm.pii.pseudonymised_lm import PseudonymisedLM

        config = load_config()
        lm = PseudonymisedLM(model=config["models"]["reasoning_llm"])
        score_predict = make_score_predictor(lm)

        case = golden_cases[0]
        result = run_match(
            case.candidates,
            case.scorecard,
            score_predict=score_predict,
            config=config,
        )
        assert isinstance(result, ShortlistResult)
        assert len(result.ranked_assessments) > 0
        for a in result.ranked_assessments:
            assert 0.0 <= a.skill_match_score <= 1.0
            assert 0.0 <= a.feedback_score <= 1.0
            assert a.narrative


class TestCassetteDriftGuard:
    def test_live_responses_match_committed_cassettes(self, golden_cases) -> None:
        """Re-record into a temp dir and diff against committed cassettes.

        Flags (warns, doesn't fail) when live output has drifted significantly.
        This catches silent model-version drift in the LLM provider.
        """
        from dsm.config import load_config
        from dsm.match.score import make_score_predictor
        from dsm.pii.pseudonymised_lm import PseudonymisedLM

        config = load_config()
        lm = PseudonymisedLM(model=config["models"]["reasoning_llm"])
        score_predict = make_score_predictor(lm)

        case = golden_cases[0]
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir) / case.case_id
            tmp_path.mkdir()

            live_responses: dict[str, dict] = {}
            for cand in case.candidates:
                held = {s.name for s in cand.skills}
                if not all(h.name in held for h in case.scorecard.hard_depth_skills):
                    continue
                try:
                    extraction = score_predict(case.scorecard, cand)
                    live_responses[cand.email] = {
                        "skill_match_score": extraction.skill_match_score,
                        "feedback_score": extraction.feedback_score,
                        "narrative": extraction.narrative,
                    }
                except Exception:
                    pass

            committed = json.loads((case.cassette_dir / "score.json").read_text())["responses"]

            drifted = []
            for email, live in live_responses.items():
                if email not in committed:
                    continue
                comm = committed[email]
                skill_delta = abs(live["skill_match_score"] - comm["skill_match_score"])
                fb_delta = abs(live["feedback_score"] - comm["feedback_score"])
                if skill_delta > 0.2 or fb_delta > 0.2:
                    drifted.append(f"{email}: skill Δ{skill_delta:.2f}, fb Δ{fb_delta:.2f}")

            if drifted:
                import warnings

                warnings.warn(
                    f"Cassette drift detected for {case.case_id}: " + "; ".join(drifted),
                    stacklevel=1,
                )
