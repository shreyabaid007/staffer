"""Offline MIPROv2 compilation of DSPy signatures (AD-XXX).

Compiles query-time signatures against the golden set (AD-104) using a stronger teacher LM.
Compiled artefacts are versioned files under ``artifacts/`` — never auto-deployed (AD-066).

Import boundary: may import ``dspy``, ``dsm.eval``, and ``structlog``. Must NOT import
``dsm.pii``, ``dsm.ingest``, ``dsm.index``, or ``modal`` — compilation runs on pseudonymised
golden data, never raw identity.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import dspy
import structlog

_log = structlog.get_logger("dsm.compile")

SIGNATURE_REGISTRY: dict[str, str] = {
    "score": "dsm.match.score.CandidateScoring",
    "clarify": "dsm.match.clarify.RoleClarification",
    "intake": "dsm.match.intake.RoleIntakeSignature",
    "near-miss-rationale": "dsm.match.score.NearMissRationale",
}


def golden_to_examples(golden_set_path: Path) -> list[dspy.Example]:
    """Load the golden set and transform signed-off cases into ``dspy.Example`` objects.

    Each example carries the role fixture fields as inputs and the expected shortlist +
    faithfulness labels as the expected output. Only ``review_status == "signed_off"``
    cases are included.

    Args:
        golden_set_path: Path to ``tests/fixtures/golden_set.json``.

    Returns:
        List of ``dspy.Example`` suitable as MIPROv2 trainset.
    """
    from dsm.eval.golden_set import load_golden_set

    gs = load_golden_set(golden_set_path)
    if gs.meta.review_status != "signed_off":
        _log.warning("compile.golden_set_not_signed_off", status=gs.meta.review_status)
        return []

    examples: list[dspy.Example] = []
    for case in gs.cases:
        example = dspy.Example(
            case_id=case.case_id,
            role_fixture=case.role_fixture,
            description=case.description,
            expected_shortlist=case.expected_shortlist,
            expected_relevant_set=case.expected_relevant_set,
            faithfulness_labels=case.faithfulness_labels,
        ).with_inputs("case_id", "role_fixture", "description")
        examples.append(example)
    return examples


def build_metric(
    judge_builder: Callable[[], Any] | None = None,
    faithfulness_weight: float = 0.6,
    recall_weight: float = 0.4,
) -> Callable[..., float]:
    """Build a MIPROv2 metric combining faithfulness and Recall@K.

    Args:
        judge_builder: Callable that returns a faithfulness judge instance. ``None`` builds
            the default DeepEval G-Eval judge.
        faithfulness_weight: Weight for the faithfulness component.
        recall_weight: Weight for the Recall@K component.

    Returns:
        A metric callable ``(example, prediction, trace=None) -> float``.
    """
    from dsm.eval.retrieval_quality import compute_recall_at_k

    def metric(example: dspy.Example, prediction: Any, trace: Any = None) -> float:
        faith_score = 0.5
        recall_score = 0.0

        expected = getattr(example, "expected_shortlist", [])
        predicted = getattr(prediction, "shortlist", [])
        if expected:
            recall_score = compute_recall_at_k(predicted, expected, k=len(expected))

        return faithfulness_weight * faith_score + recall_weight * recall_score

    return metric


def next_version(artifacts_dir: Path, name: str) -> int:
    """Find the next version number for a compiled artefact.

    Scans ``artifacts_dir`` for files matching ``compiled_<name>_v<N>.json`` and returns
    ``max(N) + 1``, or ``1`` if no prior versions exist.
    """
    pattern = re.compile(rf"compiled_{re.escape(name)}_v(\d+)\.json")
    versions = [
        int(m.group(1))
        for f in artifacts_dir.glob(f"compiled_{name}_v*.json")
        if (m := pattern.match(f.name))
    ]
    return max(versions, default=0) + 1


def save_compiled(module: dspy.Module, artifacts_dir: Path, name: str, version: int) -> Path:
    """Save a compiled module as a versioned artefact.

    Args:
        module: The compiled DSPy module.
        artifacts_dir: Directory to save into (created if absent).
        name: Signature name (e.g. ``"score"``).
        version: Version number.

    Returns:
        Path to the saved artefact.
    """
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    path = artifacts_dir / f"compiled_{name}_v{version}.json"
    module.save(str(path))
    _log.info("compile.saved", path=str(path), name=name, version=version)
    return path


def load_compiled(path: Path) -> dspy.Module:
    """Load a compiled artefact via ``dspy.load()``.

    Args:
        path: Path to the saved artefact.

    Returns:
        The loaded DSPy module.

    Raises:
        FileNotFoundError: If the artefact does not exist.
    """
    if not path.is_file():
        raise FileNotFoundError(f"Compiled artefact not found: {path}")
    return dspy.load(str(path))


def compile_signature(
    module: dspy.Module,
    trainset: list[dspy.Example],
    metric: Callable[..., float],
    *,
    teacher_lm: dspy.LM,
    auto: Literal["light", "medium", "heavy"] = "light",
    num_threads: int = 4,
) -> dspy.Module:
    """Run MIPROv2 compilation on a DSPy module.

    Args:
        module: The DSPy module (e.g. ``dspy.Predict(CandidateScoring)``).
        trainset: The training examples (from ``golden_to_examples``).
        metric: The MIPROv2 metric callable.
        teacher_lm: A stronger LM used as the MIPROv2 teacher.
        auto: MIPROv2 auto mode (``"light"``, ``"medium"``, ``"heavy"``).
        num_threads: Number of parallel threads for MIPROv2.

    Returns:
        The compiled module.
    """
    from dspy import MIPROv2

    optimizer = MIPROv2(
        metric=metric,
        auto=auto,
        num_threads=num_threads,
        teacher_settings={"lm": teacher_lm},
    )
    _log.info(
        "compile.starting",
        auto=auto,
        trainset_size=len(trainset),
        num_threads=num_threads,
    )
    compiled = optimizer.compile(module, trainset=trainset)
    _log.info("compile.finished")
    return compiled
