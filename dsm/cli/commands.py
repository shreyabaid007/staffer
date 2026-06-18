"""CLI command implementations and the match orchestrator."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any

import typer

from dsm.config import load_config
from dsm.index.stub import retrieve_candidates
from dsm.ingest.stub import get_stub_candidates, get_stub_role
from dsm.match.clarify import clarify_role
from dsm.match.gates import effective_free_date, filter_candidates
from dsm.match.rank import rank_assessments
from dsm.match.score import score_candidate
from dsm.models import (
    Candidate,
    ExclusionLog,
    ExclusionReason,
    NearMiss,
    NoMatchResult,
    ShortlistResult,
    TargetProfileScorecard,
)


def ranking_config() -> tuple[int, dict[str, Any]]:
    """Read ranking config from ``config/default.yaml`` (AD-043, AD-064).

    Rank is config-free, so the orchestrator owns this single read and the
    reproducibility snapshot it passes down.

    Returns:
        ``(top_k, config_snapshot)`` — the shortlist size and a snapshot of the weights,
        ``top_k``, and model IDs for reproducibility tracing.
    """
    config = load_config()
    top_k = int(config["ranking"]["top_k"])
    snapshot: dict[str, Any] = {
        "top_k": top_k,
        "weights": config["weights"],
        "models": config["models"],
    }
    return top_k, snapshot


def build_near_misses(
    candidates: list[Candidate],
    scorecard: TargetProfileScorecard,
    exclusion_log: ExclusionLog,
) -> list[NearMiss]:
    """Build ordered near-misses from structured data (AD-063b/c).

    Gaps are recomputed from the ``Candidate`` + ``TargetProfileScorecard`` objects — the
    human-readable ``Exclusion.detail`` is never parsed (O-NM-4). Ordering (AD-063b):
    availability misses first (smallest overshoot in days), then location misses
    (alphabetical by ``candidate_email`` — location misses have no gap metric, since any
    ``remote_eligible`` candidate already passes the gate, G-LOC-2). The full ordered list
    is returned; the orchestrator applies the top-3 cap (AD-063d).

    Args:
        candidates: the original candidate set (to look up name/location/availability).
        scorecard: the clarified role (provides the availability deadline).
        exclusion_log: the gate exclusions to turn into near-misses.

    Returns:
        Near-misses sorted per AD-063(b); unbounded (caller caps).
    """
    deadline = scorecard.start_date + timedelta(days=scorecard.availability_window_days)
    by_email = {candidate.email: candidate for candidate in candidates}

    ranked: list[tuple[tuple[int, int, str], NearMiss]] = []
    for exclusion in exclusion_log.exclusions:
        candidate = by_email.get(exclusion.candidate_email)
        if candidate is None:
            continue  # exclusion without a matching candidate — skip defensively

        if exclusion.reason is ExclusionReason.AVAILABILITY_MISMATCH:
            free_date = effective_free_date(candidate.availability)
            overshoot = (free_date - deadline).days if free_date is not None else 0
            day_word = "day" if overshoot == 1 else "days"
            gap_summary = f"available {overshoot} {day_word} after deadline"
            sort_key = (0, overshoot, candidate.email)
        else:  # LOCATION_MISMATCH
            gap_summary = f"in {candidate.location.city}, not open to relocation"
            sort_key = (1, 0, candidate.email)

        ranked.append(
            (
                sort_key,
                NearMiss(
                    candidate_email=candidate.email,
                    name=candidate.name,
                    reason=exclusion.reason.value,
                    gap_summary=gap_summary,
                ),
            )
        )

    ranked.sort(key=lambda item: item[0])
    return [near_miss for _, near_miss in ranked]


def run_match(
    candidates: list[Candidate],
    scorecard: TargetProfileScorecard,
) -> ShortlistResult | NoMatchResult:
    """Core orchestration over structured inputs: gates → (no-match | retrieve→score→rank).

    Ingest-agnostic: the CLI feeds stub data today and real ingest later, and integration
    tests inject fixtures directly. When the eligible pool is empty the orchestrator builds
    a ``NoMatchResult`` with ordered, capped near-misses (O-NM-1/2/3); otherwise it ranks.

    Args:
        candidates: the candidates to consider (already typed).
        scorecard: the clarified role requirements.

    Returns:
        A ``ShortlistResult`` when at least one candidate is eligible, else a
        ``NoMatchResult``.
    """
    eligible_pool, exclusion_log = filter_candidates(candidates, scorecard)

    if not eligible_pool.candidates:
        near_misses = build_near_misses(candidates, scorecard, exclusion_log)
        return NoMatchResult(
            role_id=scorecard.role_id,
            reason="No candidates passed the eligibility gates.",
            near_misses=near_misses[:3],  # AD-063(d): cap is a presentation decision
            exclusion_log=exclusion_log,
        )

    retrieved = retrieve_candidates(eligible_pool, scorecard, top_k=10)
    assessments = [score_candidate(candidate, scorecard) for candidate in retrieved]
    top_k, config_snapshot = ranking_config()
    return rank_assessments(assessments, scorecard.role_id, exclusion_log, top_k, config_snapshot)


def match(role_id: str = typer.Option("ROLE-STUB-01", "--role-id")) -> None:
    """Match candidates to a role (Slice 0 stub ingest, real gates + rank + no-match)."""
    role = get_stub_role()
    candidates = get_stub_candidates()

    scorecard = clarify_role(role)
    result = run_match(candidates, scorecard)

    typer.echo(result.model_dump_json(indent=2))


_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_RAW_DEFAULT = _DATA_DIR / "raw"
_BRONZE_DEFAULT = _DATA_DIR / "bronze"


def ingest(
    raw_dir: Annotated[Path, typer.Option("--raw-dir")] = _RAW_DEFAULT,
    bronze_dir: Annotated[Path, typer.Option("--bronze-dir")] = _BRONZE_DEFAULT,
    run_id: Annotated[str, typer.Option("--run-id")] = "",
) -> None:
    """Land + parse raw files into the bronze layer and print a summary."""
    import uuid

    from dsm.ingest.blobstore import LocalFSBlobStore, write_records
    from dsm.ingest.land import land
    from dsm.ingest.lineage import build_run_manifest
    from dsm.ingest.manifest import JSONLManifest
    from dsm.ingest.models import LandingStatus
    from dsm.ingest.parse import parse_blob

    if not raw_dir.is_dir():
        typer.echo(f"Raw directory not found: {raw_dir}", err=True)
        raise typer.Exit(1)

    rid = run_id or f"run-{uuid.uuid4().hex[:8]}"
    blobs = LocalFSBlobStore(bronze_dir)
    manifest = JSONLManifest(bronze_dir / "manifest.jsonl")

    typer.echo(f"Landing files from {raw_dir} …")
    entries = land(raw_dir, blobs, manifest, run_id=rid)
    run = build_run_manifest(rid, entries)

    typer.echo(f"\n── Land ── run_id={rid}")
    typer.echo(f"  landed : {run.landed}")
    typer.echo(f"  skipped: {run.skipped}")
    typer.echo(f"  invalid: {run.invalid}")

    total_records = 0
    parse_errors = 0
    for entry in entries:
        not_parseable = (
            entry.status is not LandingStatus.LANDED
            or entry.raw_bytes_hash is None
            or entry.source_type is None
        )
        if not_parseable:
            continue
        assert entry.raw_bytes_hash is not None
        assert entry.source_type is not None
        data = blobs.get(entry.raw_bytes_hash)
        try:
            records = parse_blob(
                data,
                entry.source_type,
                entry.raw_bytes_hash,
                run_id=rid,
            )
            write_records(records, entry.raw_bytes_hash, bronze_dir)
            total_records += len(records)
            name = Path(entry.source_uri).name
            stype = entry.source_type.value
            typer.echo(
                f"  parsed {stype:<20s} → {len(records):>3d} records  ({name})",
            )
        except Exception as exc:  # noqa: BLE001
            parse_errors += 1
            name = Path(entry.source_uri).name
            typer.echo(f"  PARSE ERROR {name}: {exc}", err=True)

    typer.echo("\n── Parse ──")
    typer.echo(f"  total records: {total_records}")
    if parse_errors:
        typer.echo(f"  parse errors : {parse_errors}")
        raise typer.Exit(1)
    typer.echo("")
