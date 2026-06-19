"""Observability & lineage seed (ee-ingestion-architecture §12).

The single place invalid records are logged and per-run counts are assembled. Invalids are
logged with reason + payload + run_id, never silently passed or quarantined (rule 4 of §1).
Logs are local (structlog); raw payloads are for local diagnosis only and are never shipped to
an external sink. Full quality metrics (unmapped-skill rate, conflict rate, …) are later slices.
"""

from __future__ import annotations

import structlog

from dsm.ingest.models import (
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
