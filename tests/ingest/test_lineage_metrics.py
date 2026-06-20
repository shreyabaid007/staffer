"""Quality-metrics tests for the enrich/gold stage (a-003 T-010). LN-1..4."""

from __future__ import annotations

import pytest

from dsm.ingest.lineage import (
    RunMetrics,
    build_quality_metrics,
    count_conflicts,
    coverage,
)
from dsm.ingest.models import (
    Confidence,
    FeedbackExtraction,
    GoldCandidate,
    MergedSkill,
    Sourced,
)
from dsm.models import EvidenceCitation, EvidenceSource, FreeNow, ProficiencyLevel


def _base(
    cid: str,
    *,
    skills: list[MergedSkill] | None = None,
    projects: list[str] | None = None,
    feedback: list[FeedbackExtraction] | None = None,
    conflicts: list[str] | None = None,
) -> GoldCandidate:
    return GoldCandidate(
        candidate_id=cid,
        name_vault_ref=f"name:{cid}",
        email_vault_ref=f"email:{cid}",
        availability=Sourced(value=FreeNow()),
        skills=skills or [],
        projects=projects or [],
        feedback=feedback or [],
        conflicts=conflicts or [],
        gold_hash="sha256:x",
        merge_version="merge-v1",
        prompt_version="enrich-v1",
        model_version="m",
    )


def _thin() -> GoldCandidate:
    return _base("cid:thin", skills=[MergedSkill(name="kotlin", confidence=Confidence.MEDIUM)])


def _medium() -> GoldCandidate:
    return _base(
        "cid:med",
        projects=["payments platform"],
        skills=[
            MergedSkill(
                name="kotlin", proficiency=ProficiencyLevel.EXPERT, confidence=Confidence.MEDIUM
            )
        ],
    )


def _rich() -> GoldCandidate:
    fb = FeedbackExtraction(
        sentiment="positive",
        summary="solid",
        evidence=EvidenceCitation(source=EvidenceSource.FEEDBACK, text="solid"),
    )
    return _base(
        "cid:rich",
        feedback=[fb],
        conflicts=["resume claims terraform; feedback denies it"],
        skills=[
            MergedSkill(
                name="terraform", demonstrated=False, conflict="x", confidence=Confidence.HIGH
            )
        ],
    )


def test_coverage_classifies_thin_medium_rich() -> None:
    """LN-1: coverage split is derived from the gold stream (thin/medium/rich)."""
    assert coverage([_thin(), _medium(), _rich()]) == {"thin": 1, "medium": 1, "rich": 1}


def test_count_conflicts_sums_entity_conflicts() -> None:
    assert count_conflicts([_thin(), _rich()]) == 1


def test_build_quality_metrics_assembles_summary() -> None:
    """LN-1/LN-3: summary combines stream-derived counts + run-event counters."""
    metrics = RunMetrics(leak_blocks=0, citation_verify_failures=2)
    q = build_quality_metrics([_thin(), _medium(), _rich()], run_metrics=metrics, tombstones=1)
    assert q.gold_count == 3
    assert q.coverage == {"thin": 1, "medium": 1, "rich": 1}
    assert q.conflicts == 1 and q.citation_verify_failures == 2 and q.tombstones == 1


def test_assert_clean_passes_when_no_leak() -> None:
    build_quality_metrics([_thin()], run_metrics=RunMetrics(), tombstones=0).assert_clean()


def test_assert_clean_fails_on_leak_block() -> None:
    """LN-4: a non-zero leak-block count fails the run (PII boundary breach)."""
    q = build_quality_metrics([_thin()], run_metrics=RunMetrics(leak_blocks=1), tombstones=0)
    with pytest.raises(RuntimeError):
        q.assert_clean()


def test_citation_failure_counter_is_explicit() -> None:
    """LN-3: the counter is a passed accumulator, not global state."""
    m = RunMetrics()
    from dsm.ingest.lineage import log_citation_verify_failure

    log_citation_verify_failure(
        run_id="r", candidate_id="cid:1", source_hash="h", fact="x", metrics=m
    )
    assert m.citation_verify_failures == 1
