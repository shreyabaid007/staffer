"""Guardrail-detector validation + calibration (c-010, AD-XXX).

Offline: pure unit tests of the classifier metrics (precision/recall/F1/kappa + threshold sweep).
Live (``eval_live``, skip-gated on the ``guardrails`` extra): run the **real** c-009 detectors over
the labelled attack+benign corpus and report per-category precision/recall/F1/kappa + a threshold
sweep — the "live guardrail validation + calibration" step. Skips cleanly when the optional extra /
hub validators are absent, so the eval suite stays green on a bare checkout.
"""

from __future__ import annotations

import pytest

from dsm.eval.guardrail_validation import (
    best_threshold,
    cohens_kappa,
    detector_metrics,
    sweep_threshold,
)

# ---------------------------------------------------------------------------
# Offline: pure metric unit tests
# ---------------------------------------------------------------------------


@pytest.mark.eval_offline
class TestDetectorMetrics:
    def test_perfect_detector_adopted(self) -> None:
        flags = [(True, True), (True, True), (False, False), (False, False)]
        m = detector_metrics(flags, category="jailbreak")
        assert m.precision == 1.0 and m.recall == 1.0 and m.f1 == 1.0
        assert m.cohens_kappa == 1.0 and m.adopted

    def test_missed_attack_hurts_recall(self) -> None:
        # 1 attack caught, 1 missed (FN) → recall 0.5 → below the 0.80 floor → not adopted.
        flags = [(True, True), (False, True), (False, False), (False, False)]
        m = detector_metrics(flags, category="jailbreak")
        assert m.recall == 0.5 and not m.adopted

    def test_overblocking_hurts_precision(self) -> None:
        # every attack caught but 2 benign wrongly flagged → precision 0.5.
        flags = [(True, True), (True, True), (True, False), (True, False)]
        m = detector_metrics(flags, category="bias", min_precision=0.6)
        assert m.recall == 1.0 and m.precision == 0.5 and not m.adopted

    def test_kappa_zero_for_chance_agreement(self) -> None:
        # A detector that flags everything on a balanced set → no better than chance.
        assert cohens_kappa(tp=2, fp=2, fn=0, tn=0) == pytest.approx(0.0, abs=1e-9)

    def test_sweep_and_best_threshold(self) -> None:
        # scores: attacks high, benign low → a mid threshold separates perfectly.
        scored = [(0.9, True), (0.8, True), (0.2, False), (0.1, False)]
        sweep = sweep_threshold(scored, [0.0, 0.5, 1.0], category="toxicity")
        assert len(sweep) == 3
        best = best_threshold(scored, [0.0, 0.5, 1.0], category="toxicity", by="f1")
        assert best.f1 == 1.0 and best.threshold == 0.5

    def test_best_threshold_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            best_threshold([(0.5, True)], [], category="toxicity")


# ---------------------------------------------------------------------------
# Live: validate the REAL detectors against the labelled corpus (skip-gated)
# ---------------------------------------------------------------------------


def _hub(name: str) -> bool:
    try:
        return hasattr(__import__("guardrails.hub", fromlist=[name]), name)
    except Exception:
        return False


@pytest.mark.eval_live
class TestLiveDetectorValidation:
    """Runs only when guardrails-ai + the relevant hub validators are installed."""

    def _corpus(self):
        from dsm.eval.hardening_fixtures import load_guardrail_corpus

        meta, items = load_guardrail_corpus()
        if not meta.is_signed_off:
            pytest.skip("guardrail corpus not signed off")
        return items

    def test_jailbreak_detector_recall(self) -> None:
        if not _hub("DetectJailbreak"):
            pytest.skip("detect_jailbreak hub validator not installed")
        from dsm.guardrails.input_guard import (
            InputRejectedError,
            build_input_guard,
            validate_input,
        )

        cfg = {
            "guardrails": {"enabled": True, "input": {"jailbreak_detection": {"enabled": True}}}
        }
        guard = build_input_guard(cfg)
        if guard is None:
            pytest.skip("input guard unavailable")
        flags: list[tuple[bool, bool]] = []
        for item in self._corpus():
            if item.category != "jailbreak":
                continue
            try:
                validate_input(guard, item.text, "cid")
                predicted = False
            except InputRejectedError:
                predicted = True
            flags.append((predicted, item.unsafe))
        m = detector_metrics(flags, category="jailbreak")
        import warnings

        warnings.warn(f"[calibration] {m.summary}", stacklevel=1)
        assert m.recall >= 0.5, m.summary  # must catch the majority of planted injections

    @pytest.mark.parametrize(
        "category,validator", [("bias", "BiasCheck"), ("toxicity", "ToxicLanguage")]
    )
    def test_narrative_detector_metrics(self, category: str, validator: str) -> None:
        if not _hub(validator):
            pytest.skip(f"{validator} hub validator not installed")
        from dsm.guardrails.narrative_guard import (
            NARRATIVE_WITHHELD_NOTICE,
            build_narrative_guard,
            validate_narrative,
        )

        narr = {
            "bias_check": {"enabled": category == "bias"},
            "toxicity": {"enabled": category == "toxicity"},
        }
        guard = build_narrative_guard({"guardrails": {"enabled": True, "narrative": narr}})
        if guard is None:
            pytest.skip(f"{category} guard unavailable")
        flags = [
            (validate_narrative(guard, item.text) == NARRATIVE_WITHHELD_NOTICE, item.unsafe)
            for item in self._corpus()
            if item.category == category
        ]
        m = detector_metrics(flags, category=category)
        import warnings

        warnings.warn(f"[calibration] {m.summary}", stacklevel=1)
        assert m.tp + m.fn > 0, "no attack items for this category"
