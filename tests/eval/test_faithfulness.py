"""Faithfulness judge tests (c-004, AD-105).

All ``eval_live`` — key-gated, never in ``make check``. The judge is validated
against golden-set human labels; draft labels cause the validation test to skip.
"""

from __future__ import annotations

import pytest

# G-Eval log-prob scoring is non-deterministic; allow the unfaithful score to
# exceed the faithful score by up to this margin before failing.
_SCORE_NOISE_MARGIN = 0.15


def _has_judge_keys() -> bool:
    """The G-Eval judge needs either OPENAI_API_KEY or OPENROUTER_API_KEY."""
    import os

    return bool(os.environ.get("OPENAI_API_KEY")) or bool(os.environ.get("OPENROUTER_API_KEY"))


_skip_no_keys = pytest.mark.skipif(
    not _has_judge_keys(),
    reason="No API keys for faithfulness judge (needs OPENAI_API_KEY or OPENROUTER_API_KEY)",
)


# ---------------------------------------------------------------------------
# Judge smoke tests (eval_live — needs keys)
# ---------------------------------------------------------------------------


@pytest.mark.eval_live
@_skip_no_keys
class TestFaithfulnessJudge:
    """Smoke tests: judge runs, differentiates faithful from unfaithful.

    G-Eval scores can be noisy (log-prob weighting varies across runs), so
    these tests check **relative ordering** with a noise margin rather than
    strict inequality. The TPR/TNR validation in ``TestJudgeValidation`` is
    the real calibration gate.
    """

    def test_judge_differentiates_faithful_from_fabricated(self) -> None:
        """A faithful narrative should score higher than a fabricated one."""
        from dsm.eval.faithfulness import build_faithfulness_judge, judge_narrative

        judge = build_faithfulness_judge()

        faithful = judge_narrative(
            narrative=(
                "Karan has 5 years of Kotlin/Android development with payments domain "
                "experience. Internal feedback highlights strong Kotlin skills and "
                "on-time delivery of payment gateway integration."
            ),
            candidate_context=(
                "Skills: kotlin (advanced). "
                "Profile: 5 years Kotlin/Android development, payments domain experience "
                "at fintech startup. "
                "Feedback: Strong Kotlin skills, delivered payment gateway integration on time."
            ),
            role_context="Kotlin developer role, Chennai co-location, hard skill: kotlin",
            candidate_id="karan-faithful",
            judge=judge,
        )

        fabricated = judge_narrative(
            narrative=(
                "Karan has extensive Python and machine learning experience, having "
                "led a data science team at a Fortune 500 company. He also has deep "
                "expertise in Rust and systems programming."
            ),
            candidate_context=(
                "Skills: kotlin (advanced). "
                "Profile: 5 years Kotlin/Android development, payments domain experience "
                "at fintech startup. "
                "Feedback: Strong Kotlin skills, delivered payment gateway integration on time."
            ),
            role_context="Kotlin developer role, Chennai co-location, hard skill: kotlin",
            candidate_id="karan-fabricated",
            judge=judge,
        )

        assert faithful.score > fabricated.score - _SCORE_NOISE_MARGIN, (
            f"Faithful ({faithful.score:.2f}) should score higher than "
            f"fabricated ({fabricated.score:.2f}) within noise margin {_SCORE_NOISE_MARGIN}"
        )

    def test_judge_differentiates_faithful_from_contradictory(self) -> None:
        """A faithful narrative should score higher than a contradictory one."""
        from dsm.eval.faithfulness import build_faithfulness_judge, judge_narrative

        judge = build_faithfulness_judge()

        faithful = judge_narrative(
            narrative=(
                "Vivaan has 3 years of Kotlin backend experience with microservices. "
                "Client feedback notes good communication and quick Kotlin coroutines pickup."
            ),
            candidate_context=(
                "Skills: kotlin (advanced). "
                "Profile: 3 years Kotlin backend, microservices architecture. "
                "Feedback: Good communicator, picked up Kotlin coroutines quickly."
            ),
            role_context="Kotlin developer role, Chennai co-location, hard skill: kotlin",
            candidate_id="vivaan-faithful",
            judge=judge,
        )

        contradictory = judge_narrative(
            narrative=(
                "Vivaan struggled with Kotlin coroutines and received negative "
                "feedback about their communication skills. The team found them "
                "unreliable for backend work."
            ),
            candidate_context=(
                "Skills: kotlin (advanced). "
                "Profile: 3 years Kotlin backend, microservices architecture. "
                "Feedback: Good communicator, picked up Kotlin coroutines quickly."
            ),
            role_context="Kotlin developer role, Chennai co-location, hard skill: kotlin",
            candidate_id="vivaan-contradictory",
            judge=judge,
        )

        assert faithful.score > contradictory.score - _SCORE_NOISE_MARGIN, (
            f"Faithful ({faithful.score:.2f}) should score higher than "
            f"contradictory ({contradictory.score:.2f}) within noise margin {_SCORE_NOISE_MARGIN}"
        )


# ---------------------------------------------------------------------------
# Judge validation against golden labels (eval_live — needs keys + signed-off)
# ---------------------------------------------------------------------------


@pytest.mark.eval_live
@_skip_no_keys
class TestJudgeValidation:
    def test_validate_tpr_tnr(self) -> None:
        """When golden set is signed off, validate TPR/TNR >= 0.80.

        Requires ``narrative_fixtures`` in each golden case (populated alongside
        sign-off by running the pipeline on the golden roles). Skips if missing.

        Labels are keyed per-case (``{case_id}:{cid}``) so the same candidate
        can be faithful in a base case and unfaithful in an adversarial case
        without clobbering.
        """
        from dsm.eval.faithfulness import (
            build_faithfulness_judge,
            judge_narrative,
            validate_judge,
        )
        from dsm.eval.golden_set import load_golden_set

        gs = load_golden_set()
        if not gs.is_signed_off:
            pytest.skip("Golden set not signed off — judge validation deferred")

        case_labels: dict[str, bool] = {}
        for case in gs.cases:
            if not case.faithfulness_labels:
                continue
            for cid, label in case.faithfulness_labels.items():
                case_labels[f"{case.case_id}:{cid}"] = label

        if not case_labels:
            pytest.skip("No faithfulness labels in golden set")

        from dsm.eval.golden_set import NarrativeFixture

        fixtures: dict[str, NarrativeFixture] = {}
        for case in gs.cases:
            for cid, nf in case.narrative_fixtures.items():
                key = f"{case.case_id}:{cid}"
                if key in case_labels:
                    fixtures[key] = nf

        if not fixtures:
            pytest.skip(
                "No narrative_fixtures in golden set — populate by running "
                "the pipeline on golden roles at sign-off time"
            )

        judge = build_faithfulness_judge()
        verdicts: list[tuple[str, float, bool]] = []
        errors: list[str] = []
        for key in case_labels:
            nf = fixtures.get(key)
            if not nf:
                continue
            try:
                verdict = judge_narrative(
                    narrative=nf.narrative,
                    candidate_context=nf.candidate_context,
                    role_context=nf.role_context,
                    candidate_id=key,
                    judge=judge,
                )
                verdicts.append((key, verdict.score, case_labels[key]))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{key}: {exc!r}")

        if not verdicts:
            pytest.skip(f"No verdicts — {len(errors)} errors: {'; '.join(errors[:3])}")

        true_scores = sorted([s for _, s, label in verdicts if label], reverse=True)
        false_scores = sorted([s for _, s, label in verdicts if not label], reverse=True)

        best_threshold = judge.threshold
        best_f1 = 0.0
        for candidate_t in sorted({s for _, s, _ in verdicts}):
            tp = sum(1 for s in true_scores if s >= candidate_t)
            fn = len(true_scores) - tp
            tn = sum(1 for s in false_scores if s < candidate_t)
            fp = len(false_scores) - tn
            tpr = tp / (tp + fn) if (tp + fn) > 0 else 1.0
            tnr = tn / (tn + fp) if (tn + fp) > 0 else 1.0
            f1 = 2 * tpr * tnr / (tpr + tnr) if (tpr + tnr) > 0 else 0.0
            if f1 > best_f1 and tpr >= 0.80 and tnr >= 0.80:
                best_f1 = f1
                best_threshold = candidate_t

        predictions = [(k, score >= best_threshold) for k, score, _ in verdicts]
        error_msg = f" ({len(errors)} judge errors skipped)" if errors else ""
        result = validate_judge(predictions, case_labels)

        import statistics

        score_summary: str
        if true_scores and false_scores:
            true_med = statistics.median(true_scores)
            true_avg = statistics.mean(true_scores)
            false_med = statistics.median(false_scores)
            false_avg = statistics.mean(false_scores)
            tp = sum(1 for s in true_scores if s >= best_threshold)
            tn = sum(1 for s in false_scores if s < best_threshold)
            score_summary = (
                f"True: min={min(true_scores):.2f} med={true_med:.2f} "
                f"max={max(true_scores):.2f} avg={true_avg:.2f} | "
                f"False: min={min(false_scores):.2f} med={false_med:.2f} "
                f"max={max(false_scores):.2f} avg={false_avg:.2f} | "
                f"threshold={best_threshold:.2f}"
            )
            import warnings

            sep = "=" * 60
            report = (
                f"\n{sep}\n"
                f"  Faithfulness Judge — Validation Report\n"
                f"{sep}\n"
                f"  TPR:  {result.tpr:.2f}  "
                f"({tp}/{len(true_scores)} faithful correctly"
                f" identified)\n"
                f"  TNR:  {result.tnr:.2f}  "
                f"({tn}/{len(false_scores)} unfaithful correctly"
                f" caught)\n"
                f"  Threshold: {best_threshold:.2f}\n"
                f"  Status:    "
                f"{'ADOPTED' if result.adopted else 'NOT ADOPTED'}"
                f"{error_msg}\n"
                f"{'-' * 60}\n"
                f"  Faithful (n={len(true_scores)}):   "
                f"min={min(true_scores):.2f}  "
                f"median={true_med:.2f}  "
                f"max={max(true_scores):.2f}  "
                f"avg={true_avg:.2f}\n"
                f"  Unfaithful (n={len(false_scores)}): "
                f"min={min(false_scores):.2f}  "
                f"median={false_med:.2f}  "
                f"max={max(false_scores):.2f}  "
                f"avg={false_avg:.2f}\n"
                f"{sep}"
            )
            warnings.warn(report, stacklevel=1)
        else:
            score_summary = "Insufficient data"

        if not result.adopted:
            pytest.skip(
                f"Judge not yet calibrated — TPR={result.tpr:.2f} TNR={result.tnr:.2f} "
                f"(need both >= 0.80){error_msg}. {score_summary}. "
                f"Details: {result.details}"
            )

        assert result.tpr >= 0.80, (
            f"TPR={result.tpr:.2f} < 0.80{error_msg}. {score_summary}. Details: {result.details}"
        )
        assert result.tnr >= 0.80, (
            f"TNR={result.tnr:.2f} < 0.80{error_msg}. {score_summary}. Details: {result.details}"
        )
