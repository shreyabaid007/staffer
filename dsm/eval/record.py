"""Cassette recorder: re-record golden-case LLM responses for eval (AD-093).

Run via ``make eval-record`` or ``uv run python -m dsm.eval.record``.
Calls the live clarify + score predictors over each golden case and writes
``{clarify,score}.json`` cassettes with the correct key. Skips gracefully
if API keys are absent.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
from pathlib import Path

from dsm.eval.cases import _CASSETTE_ROOT, load_golden_cases


def _has_keys() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY"))


def _prompt_hash(signature_cls: type) -> str:
    src = inspect.getsource(signature_cls)
    return hashlib.sha256(src.encode()).hexdigest()[:16]


def record_all(cassette_root: Path | None = None) -> None:
    """Re-record cassettes for all golden cases."""
    from dsm.config import load_config
    from dsm.match.clarify import RoleClarification, make_clarify_predictor
    from dsm.match.score import CandidateScoring, make_score_predictor
    from dsm.models import OpenRole
    from dsm.pii.pseudonymised_lm import PseudonymisedLM

    if not _has_keys():
        print("SKIP: OPENROUTER_API_KEY not set — cannot record cassettes.")
        return

    root = cassette_root or _CASSETTE_ROOT
    config = load_config()
    model = str(config["models"]["reasoning_llm"])
    lm = PseudonymisedLM(model=model)

    clarify_predict = make_clarify_predictor(lm)
    score_predict = make_score_predictor(lm)

    clarify_hash = _prompt_hash(RoleClarification)
    score_hash = _prompt_hash(CandidateScoring)

    cases = load_golden_cases(cassette_root=root)
    recorded = 0
    for case in cases:
        case_dir = root / case.case_id
        case_dir.mkdir(parents=True, exist_ok=True)

        role = OpenRole(
            role_id=case.scorecard.role_id,
            title=f"Role {case.case_id}",
            required_skills=[],
            location=case.scorecard.location,
            co_location_required=case.scorecard.co_location_required,
            start_date=case.scorecard.start_date,
        )
        try:
            clarification = clarify_predict(role)
            clarify_data = {
                "key": {
                    "case_id": case.case_id,
                    "signature": "clarify",
                    "prompt_hash": clarify_hash,
                    "model_version": model,
                },
                "response": clarification.model_dump(),
            }
            (case_dir / "clarify.json").write_text(json.dumps(clarify_data, indent=2) + "\n")
            print(f"  {case.case_id}/clarify.json — recorded")
        except Exception as exc:
            print(f"  {case.case_id}/clarify.json — FAILED: {exc}")

        score_responses: dict[str, dict] = {}
        for cand in case.candidates:
            held = {s.name for s in cand.skills}
            if not all(h.name in held for h in case.scorecard.hard_depth_skills):
                continue
            try:
                extraction = score_predict(case.scorecard, cand)
                score_responses[cand.email] = {
                    "skill_match_score": extraction.skill_match_score,
                    "feedback_score": extraction.feedback_score,
                    "narrative": extraction.narrative,
                    "evidence": [
                        {"source": e.source.value, "text": e.text} for e in extraction.evidence
                    ],
                }
            except Exception as exc:
                print(f"  {case.case_id}/score/{cand.email} — FAILED: {exc}")

        score_data = {
            "key": {
                "case_id": case.case_id,
                "signature": "score",
                "prompt_hash": score_hash,
                "model_version": model,
            },
            "responses": score_responses,
        }
        (case_dir / "score.json").write_text(json.dumps(score_data, indent=2) + "\n")
        n = len(score_responses)
        print(f"  {case.case_id}/score.json — {n} candidate(s) recorded")
        recorded += 1

    print(f"\nDone: {recorded}/{len(cases)} cases recorded under {root}")


if __name__ == "__main__":
    record_all()
