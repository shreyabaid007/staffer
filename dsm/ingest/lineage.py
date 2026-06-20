"""Observability & lineage seed (ee-ingestion-architecture §12).

The single place invalid records are logged and per-run counts are assembled. Invalids are
logged with reason + payload + run_id, never silently passed or quarantined (rule 4 of §1).
Logs are local (structlog); raw payloads are for local diagnosis only and are never shipped to
an external sink. Full quality metrics (unmapped-skill rate, conflict rate, …) are later slices.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from pydantic import BaseModel

from dsm.ingest.models import (
    GoldCandidate,
    LandingStatus,
    ManifestEntry,
    NormalizedRecord,
    RunManifest,
)

_log = structlog.get_logger("dsm.ingest")


def log_invalid(
    *,
    run_id: str,
    reason: str,
    payload: str,
    source_uri: str | None = None,
) -> None:
    """Log a record that failed validation and is being skipped + counted (O-LOG-1)."""
    _log.warning(
        "ingest.invalid",
        run_id=run_id,
        reason=reason,
        payload=payload,
        source_uri=source_uri,
    )


def log_unmapped_skill(
    *,
    run_id: str,
    surface_form: str,
    candidate_id: str,
) -> None:
    """Queue an unmapped skill for review (TX-2, ee-ingestion §15#6).

    The "queue" is the structured log stream + the derived count; no global mutable counter
    is kept, so the metric stays deterministic and replay-stable. ``surface_form`` is a skill
    name (non-PII); ``candidate_id`` is the tokenized id, never the raw email.
    """
    _log.info(
        "ingest.unmapped_skill",
        run_id=run_id,
        surface_form=surface_form,
        candidate_id=candidate_id,
    )


def count_unmapped_skills(records: list[NormalizedRecord]) -> int:
    """Per-run unmapped-skill count, derived from the record stream (metrics seed, §12)."""
    return sum(1 for record in records for skill in record.skills if skill.unmapped)


# ---------------------------------------------------------------------------
# Enrich/gold quality metrics (a-003 §12) — LN-1..4
# ---------------------------------------------------------------------------


@dataclass
class RunMetrics:
    """Run-event accumulator for events not derivable from the gold stream (passed, not global)."""

    leak_blocks: int = 0
    citation_verify_failures: int = 0


def log_citation_verify_failure(
    *,
    run_id: str,
    candidate_id: str,
    source_hash: str,
    fact: str,
    metrics: RunMetrics | None = None,
) -> None:
    """A claim whose quote was not found verbatim in the source — dropped + counted (EN-4/§12)."""
    _log.warning(
        "ingest.citation_verify_failed",
        run_id=run_id,
        candidate_id=candidate_id,
        source_hash=source_hash,
        fact=fact,
    )
    if metrics is not None:
        metrics.citation_verify_failures += 1


def log_leak_block(
    *,
    run_id: str,
    candidate_id: str,
    source_hash: str,
    hit_count: int,
    metrics: RunMetrics | None = None,
) -> None:
    """The outbound leak-scan blocked a call (AD-069). Logs a count, never the PII value (LN-4)."""
    _log.error(
        "ingest.leak_block",
        run_id=run_id,
        candidate_id=candidate_id,
        source_hash=source_hash,
        hit_count=hit_count,
    )
    if metrics is not None:
        metrics.leak_blocks += 1


def log_conflict(*, run_id: str, candidate_id: str, detail: str) -> None:
    """A resume↔feedback disagreement recorded on the gold entity (MG-5/§12)."""
    _log.info("ingest.conflict", run_id=run_id, candidate_id=candidate_id, detail=detail)


def log_tombstone(*, run_id: str, candidate_id: str) -> None:
    """A consultant who disappeared from the latest snapshot (AD-070/RC-1)."""
    _log.info("ingest.tombstone", run_id=run_id, candidate_id=candidate_id)


def _profile_shape(gold: GoldCandidate) -> str:
    """Classify gold coverage: rich (has feedback) > medium (resume signal) > thin (CSV-only)."""
    if gold.feedback:
        return "rich"
    has_resume_signal = (
        bool(gold.projects)
        or bool(gold.domains)
        or any(s.proficiency is not None for s in gold.skills)
    )
    return "medium" if has_resume_signal else "thin"


def coverage(gold: list[GoldCandidate]) -> dict[str, int]:
    """Profile-coverage split for the run (thin/medium/rich), derived from the gold stream."""
    counts = {"thin": 0, "medium": 0, "rich": 0}
    for g in gold:
        counts[_profile_shape(g)] += 1
    return counts


def count_conflicts(gold: list[GoldCandidate]) -> int:
    """Total recorded resume↔feedback conflicts across the run (the conflict-rate numerator)."""
    return sum(len(g.conflicts) for g in gold)


class QualityMetrics(BaseModel):
    """The run's quality summary (§12). ``leak_blocks > 0`` is an invariant breach (LN-4)."""

    gold_count: int
    coverage: dict[str, int]
    conflicts: int
    citation_verify_failures: int
    leak_blocks: int
    tombstones: int

    def assert_clean(self) -> None:
        """Hard invariant: any leak block means PII almost reached the LLM — fail the run."""
        if self.leak_blocks > 0:
            raise RuntimeError(
                f"{self.leak_blocks} leak-scan block(s) this run — PII boundary breach (AD-069)"
            )


def build_quality_metrics(
    gold: list[GoldCandidate],
    *,
    run_metrics: RunMetrics,
    tombstones: int,
) -> QualityMetrics:
    """Assemble the run quality summary from the gold stream + run-event counters (LN-1/LN-3)."""
    return QualityMetrics(
        gold_count=len(gold),
        coverage=coverage(gold),
        conflicts=count_conflicts(gold),
        citation_verify_failures=run_metrics.citation_verify_failures,
        leak_blocks=run_metrics.leak_blocks,
        tombstones=tombstones,
    )


def build_run_manifest(
    run_id: str,
    entries: list[ManifestEntry],
    *,
    parse_invalid: int = 0,
) -> RunManifest:
    """Tally landed/skipped/invalid for the run. ``parse_invalid`` adds parse-step skips
    (malformed rows, unreadable PDFs, keyless feedback) to the file-level INVALID count."""
    landed = sum(1 for e in entries if e.status is LandingStatus.LANDED)
    skipped = sum(1 for e in entries if e.status is LandingStatus.SKIPPED)
    invalid = sum(1 for e in entries if e.status is LandingStatus.INVALID) + parse_invalid
    return RunManifest(
        run_id=run_id,
        entries=entries,
        landed=landed,
        skipped=skipped,
        invalid=invalid,
    )
