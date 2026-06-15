"""Deterministic ranking — sort, tie-break, and top-k truncation.

Rank is config-free (the refinement to T-004): ``top_k`` and ``config_snapshot`` are
supplied by the orchestrator, which owns the single read of ``config/default.yaml``.
This avoids two defaults (function arg + config) silently diverging. Rank never builds a
``NoMatchResult`` — the orchestrator handles the empty-pool path (AD-063c).
"""

from __future__ import annotations

from typing import Any

from dsm.models import CandidateAssessment, ExclusionLog, ShortlistResult


def rank_assessments(
    assessments: list[CandidateAssessment],
    role_id: str,
    exclusion_log: ExclusionLog,
    top_k: int,
    config_snapshot: dict[str, Any],
) -> ShortlistResult:
    """Sort assessments, break ties deterministically, and keep the top ``top_k``.

    Sort order (R-SORT-1, R-TIE-1): ``combined_score`` desc, then ``hard_skill_coverage``
    desc, then ``desired_skill_coverage`` desc, then ``candidate.email`` ascending. The
    email tie-break guarantees a single deterministic ordering for identical scores.

    Args:
        assessments: scored candidates to rank (may be empty).
        role_id: the role these assessments belong to.
        exclusion_log: gate exclusions, passed through onto the result for traceability.
        top_k: maximum number of assessments to return (required; orchestrator reads
            ``ranking.top_k`` from config and passes it in — no default here, AD-043).
        config_snapshot: reproducibility snapshot (weights, top_k, model IDs) built by the
            orchestrator; embedded verbatim on the result. Rank does not read config.

    Returns:
        A ``ShortlistResult`` with the ranked top-k, ``total_eligible`` = number of
        assessments received, the exclusion log, and the config snapshot. An empty
        ``assessments`` yields an empty ``ranked_assessments`` (R-OUT-1).
    """
    ranked = sorted(
        assessments,
        key=lambda a: (
            -a.combined_score,
            -a.hard_skill_coverage,
            -a.desired_skill_coverage,
            a.candidate.email,
        ),
    )[:top_k]
    return ShortlistResult(
        role_id=role_id,
        ranked_assessments=ranked,
        total_eligible=len(assessments),
        exclusion_log=exclusion_log,
        config_snapshot=config_snapshot,
    )
