"""Counterfactual fairness parity (c-010, AD-XXX).

`product.md` promises "fairness is something we test for, not something we claim" — this backs
that promise with a **paired-profile counterfactual** eval: take a candidate, produce a variant
that differs **only** in demographic-*proxy* signals (gender-coded names/pronouns, school- or
employer-prestige tokens in the profile summary + feedback) with skills / availability / location
held constant, and assert the outcome is invariant within a tolerance.

Two layers (both run under ``make eval``, mirroring the deterministic-vs-live split of the AI eval
layer):

- **Deterministic (offline, always runs):** the gates + hard/desired coverage + combine are proven
  **proxy-blind by construction** — swapping proxy text leaves ``combined_score`` / coverages /
  rank identical (the deterministic layer keys on skills/location/availability, never prose). The
  test wires this through ``run_match`` with a cassette; this module supplies the comparison.
- **Live (key-gated):** feed the paired profiles to the **real** score LLM; assert the sub-scores
  stay within ``tolerance`` — the layer where bias can actually enter. Non-gating; reports the
  largest disparity + any violations.

Pure + dependency-free — no LLM, no pipeline import; the tests wire the pipeline and pass scores
here for comparison.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Default parity tolerance on a [0,1] sub-score. A swap of a demographic proxy should move a
# sub-score by less than this; larger ⇒ the model is keying on the proxy (a fairness violation).
DEFAULT_TOLERANCE = 0.05


@dataclass(frozen=True)
class ParityResult:
    """Sub-score parity between a baseline profile and its proxy-swapped counterfactual."""

    pair_id: str
    max_delta: float
    within_tolerance: bool
    deltas: dict[str, float]


@dataclass(frozen=True)
class FairnessReport:
    """Aggregate parity across all counterfactual pairs."""

    total: int
    violations: int
    max_delta: float
    tolerance: float
    passed: bool

    @property
    def summary(self) -> str:
        return (
            f"fairness parity: {self.total - self.violations}/{self.total} pairs within "
            f"tolerance {self.tolerance:.2f} — max_delta={self.max_delta:.3f} — "
            f"{'PASS' if self.passed else 'FAIL'}"
        )


def parity(
    pair_id: str,
    baseline: dict[str, float],
    variant: dict[str, float],
    *,
    tolerance: float = DEFAULT_TOLERANCE,
) -> ParityResult:
    """Compare per-field sub-scores of a baseline vs its proxy-swapped variant.

    Args:
        pair_id: identifier for the counterfactual pair (for the report).
        baseline: ``{field: score}`` for the original profile (e.g. ``skill_match``, ``feedback``,
            ``combined``).
        variant: ``{field: score}`` for the proxy-swapped profile — **same keys** as ``baseline``.
        tolerance: max allowed absolute delta per field.

    Returns:
        A :class:`ParityResult`; ``within_tolerance`` is True iff **every** shared field's absolute
        delta is ``<= tolerance``.
    """
    fields = baseline.keys() & variant.keys()
    deltas = {field: abs(baseline[field] - variant[field]) for field in fields}
    max_delta = max(deltas.values(), default=0.0)
    return ParityResult(
        pair_id=pair_id,
        max_delta=max_delta,
        within_tolerance=max_delta <= tolerance,
        deltas=deltas,
    )


def aggregate_parity(
    results: list[ParityResult], *, tolerance: float = DEFAULT_TOLERANCE
) -> FairnessReport:
    """Fold per-pair parity results into a pass/fail :class:`FairnessReport`.

    Passes iff **no** pair breaches the tolerance. An empty set passes vacuously (nothing to do).
    """
    violations = sum(1 for r in results if not r.within_tolerance)
    max_delta = max((r.max_delta for r in results), default=0.0)
    report = FairnessReport(
        total=len(results),
        violations=violations,
        max_delta=max_delta,
        tolerance=tolerance,
        passed=violations == 0,
    )
    logger.info("Fairness: %s", report.summary)
    return report
