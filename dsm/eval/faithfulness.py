"""DeepEval G-Eval faithfulness judge (c-004, AD-105).

Scores whether a candidate narrative follows from cited evidence + candidate
data. Validated against the golden set — adopted only if TPR/TNR >= 0.80.

Does NOT evaluate objective properties already covered by the six deterministic
invariants (gates, PII, citation presence, hard-skill-exclusion, determinism,
adjacency-flag).

DeepEval imports are deferred to function bodies so the module is importable
in offline contexts without triggering provider connections.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deepeval.metrics import GEval

logger = logging.getLogger(__name__)

FAITHFULNESS_CRITERIA = """\
Evaluate whether the narrative assessment of the candidate faithfully represents
the evidence and candidate data provided.

Score on a 1–10 scale:
- 9–10: Every claim traces directly to the evidence. Phrasing is proportionate
  to the source material — no inflation or embellishment.
- 7–8: All substantive claims are supported. Minor wording choices are slightly
  generous or vague, but nothing materially misleads.
- 4–6: Some claims are supported but the narrative noticeably embellishes,
  exaggerates qualifications, adds plausible-sounding details not in the evidence,
  or mischaracterises the strength of the feedback.
- 1–3: The narrative fabricates claims, directly contradicts the evidence, or
  attributes specific achievements, metrics, or credentials not present in the
  candidate data.

Evaluate these dimensions:
1. Does the narrative make claims not supported by the cited evidence? (fabrication)
2. Does the narrative contradict the candidate's skills, feedback, or profile? (contradiction)
3. Does the narrative inflate or exaggerate qualifications beyond what the \
evidence supports? (embellishment)
4. Is the characterisation of skill fit consistent with the sub-scores? (consistency)

Do NOT evaluate: whether gates are correctly applied (location/availability
filtering), whether PII is present, whether citations exist as verbatim quotes,
or whether the candidate should have been excluded — these are checked by
separate deterministic tests."""


@dataclass(frozen=True)
class FaithfulnessVerdict:
    """Per-candidate faithfulness judgement."""

    candidate_id: str
    score: float
    faithful: bool
    reason: str


@dataclass(frozen=True)
class JudgeValidation:
    """TPR/TNR validation of the judge against human labels."""

    tpr: float
    tnr: float
    adopted: bool
    details: str


def _build_judge_model():
    """Build the LLM model for the judge, preferring OpenRouter when available.

    Checks ``OPENROUTER_API_KEY`` first (OpenAI-compatible endpoint). Falls
    back to ``None`` (DeepEval's default OpenAI model) only when no
    OpenRouter key is set and ``OPENAI_API_KEY`` is present.
    """
    import os

    try:
        from deepeval.models.llms.openai_model import GPTModel
    except ImportError as exc:
        raise ImportError(
            "Cannot import GPTModel from deepeval.models.llms.openai_model — "
            "the internal path may have changed in your deepeval version."
        ) from exc

    openrouter_key = os.environ.get("OPENROUTER_API_KEY")

    if openrouter_key:
        return GPTModel(
            model="anthropic/claude-sonnet-4-6",
            api_key=openrouter_key,
            base_url="https://openrouter.ai/api/v1",
        )
    return None


def build_faithfulness_judge() -> GEval:
    """Construct the G-Eval faithfulness metric.

    Prefers ``OPENROUTER_API_KEY`` when set (OpenAI-compatible endpoint).
    Falls back to DeepEval's default OpenAI model when only
    ``OPENAI_API_KEY`` is present.

    Returns:
        A configured ``deepeval.metrics.GEval`` instance.
    """
    from deepeval.metrics import GEval
    from deepeval.test_case.llm_test_case import SingleTurnParams

    model = _build_judge_model()
    kwargs: dict = {
        "name": "Narrative Faithfulness",
        "criteria": FAITHFULNESS_CRITERIA,
        "evaluation_params": [
            SingleTurnParams.INPUT,
            SingleTurnParams.ACTUAL_OUTPUT,
        ],
        "threshold": 0.5,
    }
    if model is not None:
        kwargs["model"] = model
    return GEval(**kwargs)


def judge_narrative(
    narrative: str,
    candidate_context: str,
    role_context: str,
    candidate_id: str = "",
    *,
    judge: GEval | None = None,
) -> FaithfulnessVerdict:
    """Run the G-Eval faithfulness judge on a single narrative.

    Args:
        narrative: The candidate assessment narrative to evaluate.
        candidate_context: Candidate skills, feedback, profile_summary
            concatenated as context.
        role_context: Role description / scorecard summary.
        candidate_id: For labelling the verdict.
        judge: Pre-built GEval instance. Built fresh if ``None``.

    Returns:
        A ``FaithfulnessVerdict`` with score and faithful flag.
    """
    from deepeval.test_case import LLMTestCase

    if judge is None:
        judge = build_faithfulness_judge()
    test_case = LLMTestCase(
        input=f"Role: {role_context}\nCandidate: {candidate_context}",
        actual_output=narrative,
    )
    judge.measure(test_case)
    score = judge.score if judge.score is not None else 0.0
    return FaithfulnessVerdict(
        candidate_id=candidate_id,
        score=score,
        faithful=score >= judge.threshold,
        reason=judge.reason or "",
    )


def validate_judge(
    predictions: list[tuple[str, bool]],
    labels: dict[str, bool],
) -> JudgeValidation:
    """Compute TPR/TNR of the judge against human labels.

    Args:
        predictions: List of ``(candidate_id, judge_says_faithful)`` pairs.
        labels: ``{candidate_id: human_label}`` ground truth.

    Returns:
        A ``JudgeValidation`` with TPR, TNR, and adopted flag.
    """
    tp = fn = fp = tn = 0
    for cid, predicted_faithful in predictions:
        if cid not in labels:
            continue
        actual = labels[cid]
        if actual and predicted_faithful:
            tp += 1
        elif actual and not predicted_faithful:
            fn += 1
        elif not actual and predicted_faithful:
            fp += 1
        else:
            tn += 1

    total = tp + fn + fp + tn
    if total == 0:
        details = "No predictions matched any labels — cannot validate"
        logger.warning("Judge validation: %s", details)
        return JudgeValidation(tpr=0.0, tnr=0.0, adopted=False, details=details)

    tpr = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    tnr = tn / (tn + fp) if (tn + fp) > 0 else 1.0
    adopted = tpr >= 0.80 and tnr >= 0.80

    details = (
        f"TP={tp} FN={fn} FP={fp} TN={tn} — "
        f"TPR={tpr:.2f} TNR={tnr:.2f} — "
        f"{'ADOPTED' if adopted else 'NOT ADOPTED (below 80% threshold)'}"
    )
    logger.info("Judge validation: %s", details)
    return JudgeValidation(tpr=tpr, tnr=tnr, adopted=adopted, details=details)
