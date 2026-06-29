"""NL-intake parse-quality eval (c-006; AD-XXX) — Tier-2 offline + Tier-3 live smoke.

- **Offline** (``eval_offline``, deterministic, no network): replays each case's signed-off
  ``recorded_intake`` (the "golden parse") through ``assemble_role`` and asserts the expected
  ``OpenRole`` / ``ClarificationNeeded``. Pins parse→assemble accuracy + guards regressions.
- **Live** (``eval_live``, key-gated): runs the **real** intake predictor (``PseudonymisedLM``) on
  the prose and asserts the parse assembles to the expected **structural** fields (location,
  co-location, hard skills, never-guessed missing fields). The start date is checked only for
  validity/window — the live LLM resolves relative phrases against the real today.

Mirrors ``test_signatures`` (Tier-2 offline) + ``test_live_smoke`` (Tier-3 live). Runs under
``make eval`` only; the deterministic ``assemble_role`` logic itself is unit-tested in
``tests/match/test_intake.py`` (which runs in ``make check``).
"""

from __future__ import annotations

from datetime import date

import pytest

from dsm.config import load_config
from dsm.eval.nl_intake_golden import NLIntakeCase, load_nl_intake_golden
from dsm.match.intake import ClarificationNeeded, assemble_role
from dsm.models import OpenRole, SkillDepth
from tests.eval.conftest import has_keys

_GOLDEN = load_nl_intake_golden()
_FIXTURE_TODAY = date.fromisoformat(_GOLDEN.meta.today)
_MAX_HORIZON = int(load_config()["nl_intake"]["max_horizon_days"])
_LIVE_CASES = [c for c in _GOLDEN.cases if c.live]


def _assemble(intake, today: date, case_id: str):
    return assemble_role(intake, today, max_horizon_days=_MAX_HORIZON, role_id=f"NL-{case_id}")


def _hard(role: OpenRole) -> list[str]:
    return [s.name for s in role.required_skills if s.depth == SkillDepth.HARD]


def _desired(role: OpenRole) -> list[str]:
    return [s.name for s in role.required_skills if s.depth == SkillDepth.DESIRED]


# ---------------------------------------------------------------------------
# Tier-2: deterministic parse-accuracy over the signed-off golden parses
# ---------------------------------------------------------------------------


@pytest.mark.eval_offline
class TestNLIntakeOffline:
    def test_golden_set_signed_off(self) -> None:
        assert _GOLDEN.is_signed_off, "NL-intake golden labels must be human-signed-off"

    @pytest.mark.parametrize("case", _GOLDEN.cases, ids=lambda c: c.case_id)
    def test_golden_parse_assembles_as_expected(self, case: NLIntakeCase) -> None:
        result = _assemble(case.recorded_intake, _FIXTURE_TODAY, case.case_id)
        exp = case.expected

        if exp.outcome == "clarification":
            assert isinstance(result, ClarificationNeeded), (
                f"{case.case_id}: expected clarification"
            )
            assert result.missing == exp.missing
            return

        assert isinstance(result, OpenRole), f"{case.case_id}: expected a role"
        assert result.location.city == exp.location_city
        assert result.location.remote_within_country == exp.remote_within_country
        assert result.co_location_required == exp.co_location_required  # Python-derived (FR-8)
        assert _hard(result) == exp.hard_skills
        assert _desired(result) == exp.desired_skills
        if exp.start_date is not None:
            assert result.start_date.isoformat() == exp.start_date


# ---------------------------------------------------------------------------
# Tier-3: real-LLM parse smoke (structural fields; date checked for validity only)
# ---------------------------------------------------------------------------


@pytest.mark.eval_live
@pytest.mark.skipif(not has_keys(), reason="No API keys for live NL-intake eval")
class TestNLIntakeLive:
    @pytest.mark.parametrize("case", _LIVE_CASES, ids=lambda c: c.case_id)
    def test_real_llm_parses_to_expected(self, case: NLIntakeCase) -> None:
        from dsm.match.intake import make_intake_predictor
        from dsm.pii.pseudonymised_lm import PseudonymisedLM

        config = load_config()
        predict = make_intake_predictor(
            PseudonymisedLM(
                model=config["models"]["reasoning_llm"],
                temperature=config["nl_intake"]["temperature"],
            )
        )
        today = date.today()
        intake = predict(case.prose, today)  # the REAL parse
        result = _assemble(intake, today, case.case_id)
        exp = case.expected

        if exp.outcome == "clarification":
            assert isinstance(result, ClarificationNeeded), (
                f"{case.case_id}: real LLM should leave {exp.missing} unfilled (never-guess)"
            )
            assert set(exp.missing) <= set(result.missing)
            return

        assert isinstance(result, OpenRole), f"{case.case_id}: real LLM should yield a role"
        assert (result.location.city or "").casefold() == (exp.location_city or "").casefold()
        assert result.location.remote_within_country == exp.remote_within_country
        assert result.co_location_required == exp.co_location_required
        assert set(exp.hard_skills) <= set(_hard(result)), (
            f"{case.case_id}: expected hard skills {exp.hard_skills} not all present in "
            f"{_hard(result)}"
        )
        if exp.start_date is not None:
            # Live LLM resolves relative to the real today → check validity/window, not value.
            assert today <= result.start_date
