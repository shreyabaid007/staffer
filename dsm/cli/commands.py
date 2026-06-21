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
    (alphabetical by ``candidate_email`` — location misses have no gap metric, since the
    onsite gate is structural: the candidate's city is neither the role city nor in their
    ``onsite_cities``, AD-086). The full ordered list is returned; the orchestrator applies
    the top-3 cap (AD-063d).

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
            cand_city = candidate.location.city or "no base city"
            role_city = scorecard.location.city or "required city"
            gap_summary = f"in {cand_city}, not in onsite set for {role_city}"
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
_SILVER_DEFAULT = _DATA_DIR / "silver"
_GOLD_DEFAULT = _DATA_DIR / "gold"


def _bronze_cell(raw: dict[str, str | list[str]], *names: str) -> str:
    """Case/whitespace-insensitive lookup of a bronze CSV cell (mirrors silver._cell)."""
    norm = {k.strip().lower(): v for k, v in raw.items()}
    for name in names:
        value = norm.get(name.strip().lower())
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _build_resume_predictor(config: dict[str, Any]):  # pragma: no cover - exercised only live
    """Build the live resume predictor over PseudonymisedLM (monkeypatched in CLI tests)."""
    from dsm.ingest.enrich import make_resume_predictor
    from dsm.pii.pseudonymised_lm import PseudonymisedLM

    lm = PseudonymisedLM(model=config["models"]["reasoning_llm"])
    return make_resume_predictor(lm)


def _build_feedback_predictor(config: dict[str, Any]):  # pragma: no cover - exercised only live
    """Build the live feedback predictor over PseudonymisedLM (monkeypatched in CLI tests)."""
    from dsm.ingest.enrich import make_feedback_predictor
    from dsm.pii.pseudonymised_lm import PseudonymisedLM

    lm = PseudonymisedLM(model=config["models"]["reasoning_llm"])
    return make_feedback_predictor(lm)


def _build_embed_client():  # pragma: no cover - exercised only live (monkeypatched in CLI tests)
    """Build the live Modal embed client (monkeypatched to a FakeEmbedClient in CLI tests)."""
    from dsm.index.embed_client import ModalEmbedClient

    return ModalEmbedClient()


def ingest(
    raw_dir: Annotated[Path, typer.Option("--raw-dir")] = _RAW_DEFAULT,
    bronze_dir: Annotated[Path, typer.Option("--bronze-dir")] = _BRONZE_DEFAULT,
    silver_dir: Annotated[Path, typer.Option("--silver-dir")] = _SILVER_DEFAULT,
    gold_dir: Annotated[Path, typer.Option("--gold-dir")] = _GOLD_DEFAULT,
    run_id: Annotated[str, typer.Option("--run-id")] = "",
) -> None:
    """Land + parse → bronze, normalize → silver, enrich + merge → gold, and print a summary."""
    import os
    import uuid

    from dsm.ingest.blobstore import LocalFSBlobStore, write_records
    from dsm.ingest.land import land
    from dsm.ingest.lineage import build_run_manifest, count_unmapped_skills
    from dsm.ingest.manifest import JSONLManifest
    from dsm.ingest.models import BronzeRecord, LandingStatus, NormalizedRecord, SourceType
    from dsm.ingest.parse import parse_blob
    from dsm.ingest.silver import normalize_run, write_normalized
    from dsm.ingest.taxonomy import load_taxonomy

    _SUPPLY_TYPES = {
        SourceType.SUPPLY_BEACH,
        SourceType.SUPPLY_ROLLING_OFF,
        SourceType.SUPPLY_NEW_JOINERS,
    }

    if not os.environ.get("DSM_CANDIDATE_ID_KEY"):
        typer.echo(
            "DSM_CANDIDATE_ID_KEY is not set — required to derive candidate_id for silver "
            "(AD-067). Set it and re-run.",
            err=True,
        )
        raise typer.Exit(1)

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

    taxonomy = load_taxonomy()
    total_records = 0
    parse_errors = 0
    silver_errors = 0
    silver_skipped = 0
    all_normalized: list[NormalizedRecord] = []
    bronze_supply: list[BronzeRecord] = []
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
            if entry.source_type in _SUPPLY_TYPES:
                bronze_supply.extend(records)  # source of name+email for redaction/vault refs
            name = Path(entry.source_uri).name
            stype = entry.source_type.value
            typer.echo(
                f"  parsed {stype:<20s} → {len(records):>3d} records  ({name})",
            )
        except Exception as exc:  # noqa: BLE001
            parse_errors += 1
            name = Path(entry.source_uri).name
            typer.echo(f"  PARSE ERROR {name}: {exc}", err=True)
            continue
        try:
            normalized = normalize_run(
                records,
                snapshot_dates={entry.raw_bytes_hash: entry.snapshot_date},
                taxonomy=taxonomy,
                run_id=rid,
            )
            write_normalized(normalized, entry.raw_bytes_hash, silver_dir)
            silver_skipped += len(records) - len(normalized)
            all_normalized.extend(normalized)
        except Exception as exc:  # noqa: BLE001
            silver_errors += 1
            typer.echo(f"  SILVER ERROR {Path(entry.source_uri).name}: {exc}", err=True)

    typer.echo("\n── Parse ──")
    typer.echo(f"  total records: {total_records}")
    if parse_errors:
        typer.echo(f"  parse errors : {parse_errors}")

    # ── Silver ── PII-safe summary only: counts + a per-record line that never echoes raw_text.
    avail_counts = {"free_now": 0, "rolling_off": 0, "new_joiner": 0}
    for record in all_normalized:
        if record.availability is not None:
            avail_counts[record.availability.type] += 1
    with_warnings = sum(1 for r in all_normalized if r.parse_warnings)
    typer.echo("\n── Silver ──")
    typer.echo(f"  normalized      : {len(all_normalized)}")
    typer.echo(f"  coercion-skipped: {silver_skipped}")
    typer.echo(
        f"  availability    : free_now={avail_counts['free_now']} "
        f"rolling_off={avail_counts['rolling_off']} new_joiner={avail_counts['new_joiner']}"
    )
    typer.echo(f"  unmapped skills : {count_unmapped_skills(all_normalized)}")
    typer.echo(f"  w/ warnings     : {with_warnings}")
    for record in all_normalized:
        typer.echo(
            f"    {record.candidate_id} {record.source_type.value} "
            f"avail={record.availability.type if record.availability else 'none'} "
            f"skills={len(record.skills)} warn={len(record.parse_warnings)}"
        )
    if silver_errors:
        typer.echo(f"  silver errors   : {silver_errors}", err=True)

    # ── Gold ── enrich (PII-bracketed) + merge → one canonical entity per candidate_id.
    from collections import defaultdict
    from datetime import date

    from dsm.config import load_config
    from dsm.ingest.enrich import enrich_feedback, enrich_resume
    from dsm.ingest.goldstore import list_gold_ids, read_gold, write_gold
    from dsm.ingest.lineage import (
        RunMetrics,
        build_quality_metrics,
        log_leak_block,
        log_tombstone,
    )
    from dsm.ingest.merge import merge_run
    from dsm.ingest.models import FeedbackExtraction, ProfileSummaryExtraction
    from dsm.ingest.reconcile import freshness_guard, reconcile, tombstone
    from dsm.pii.leakscan import PIILeakError
    from dsm.pii.vault import InMemoryVault
    from dsm.pii.vault import candidate_id as derive_cid

    config = load_config()
    vault = InMemoryVault()
    identities: dict[str, tuple[str, str]] = {}
    known_pii: dict[str, list[str]] = {}
    for br in bronze_supply:
        email = _bronze_cell(br.raw, "Email")
        if not email:
            continue
        cid = derive_cid(email)
        name = _bronze_cell(br.raw, "Name")
        identities[cid] = vault.put_identity(cid, name, email)
        known_pii[cid] = [p for p in (name, email) if p]

    metrics = RunMetrics()
    profiles: dict[str, ProfileSummaryExtraction] = {}
    feedbacks: dict[str, list[FeedbackExtraction]] = defaultdict(list)
    resume_recs = [r for r in all_normalized if r.source_type is SourceType.RESUME]
    feedback_recs = [r for r in all_normalized if r.source_type is SourceType.FEEDBACK]
    try:
        if resume_recs:
            resume_predict = _build_resume_predictor(config)
            for r in resume_recs:
                ext = enrich_resume(
                    r,
                    known_pii=known_pii.get(r.candidate_id, []),
                    predict=resume_predict,
                    run_id=rid,
                    metrics=metrics,
                )
                if ext is not None:
                    profiles[r.candidate_id] = ext
        if feedback_recs:
            feedback_predict = _build_feedback_predictor(config)
            for r in feedback_recs:
                ext = enrich_feedback(
                    r,
                    known_pii=known_pii.get(r.candidate_id, []),
                    predict=feedback_predict,
                    run_id=rid,
                    metrics=metrics,
                )
                if ext is not None:
                    feedbacks[r.candidate_id].append(ext)
    except PIILeakError:
        log_leak_block(run_id=rid, candidate_id="?", source_hash="?", hit_count=1, metrics=metrics)
        typer.echo("  GOLD ABORTED: outbound leak-scan blocked a call (AD-069)", err=True)
        raise typer.Exit(1) from None

    gold = merge_run(
        all_normalized,
        profiles=profiles,
        feedbacks=dict(feedbacks),
        identities=identities,
        taxonomy=taxonomy,
        prompt_version=config["enrich"]["prompt_version"],
        model_version=config["models"]["reasoning_llm"],
    )

    typer.echo("\n── Gold ──")
    if not gold:
        # No candidates processed this run — an idempotent re-run (all files SKIPPED at landing) or
        # a run that landed nothing. This is NOT a current snapshot, so we must NOT tombstone prior
        # gold (that would flag every consultant as departed). Replay-from-bronze + an enrichment
        # cache (§11) are deferred, so a no-data run is a no-op for gold: leave it untouched.
        typer.echo("  no candidates processed this run — gold left unchanged (no reconcile)")
        typer.echo("  (re-run is idempotent; to rebuild gold, re-land changed inputs)")
        if parse_errors or silver_errors:
            raise typer.Exit(1)
        typer.echo("")
        return

    prior_ids = list_gold_ids(gold_dir)
    rec = reconcile({g.candidate_id for g in gold}, prior_ids)
    for g in gold:
        write_gold(g, gold_dir)
    for cid in rec.tombstoned_ids:
        prior = read_gold(cid, gold_dir)
        if prior is not None:
            write_gold(tombstone(prior), gold_dir)
            log_tombstone(run_id=rid, candidate_id=cid)
    latest_as_of = max((g.valid_as_of for g in gold if g.valid_as_of), default=None)
    fresh_warnings = freshness_guard(
        latest_as_of,
        max_staleness_days=config["reconcile"]["max_staleness_days"],
        today=date.today(),
    )
    quality = build_quality_metrics(gold, run_metrics=metrics, tombstones=len(rec.tombstoned_ids))

    typer.echo(f"  entities    : {quality.gold_count}")
    cov = quality.coverage
    typer.echo(f"  coverage    : thin={cov['thin']} medium={cov['medium']} rich={cov['rich']}")
    typer.echo(f"  conflicts   : {quality.conflicts}")
    typer.echo(f"  citation-verify failures: {quality.citation_verify_failures}")
    typer.echo(f"  leak-scan hits: {quality.leak_blocks}")
    typer.echo(f"  tombstones  : {quality.tombstones}")
    for warning in fresh_warnings:
        typer.echo(f"  freshness   : {warning}")
    for g in gold:  # PII-safe per-entity line: candidate_id token + structured fields only
        typer.echo(
            f"    {g.candidate_id} grade={g.grade.value.value if g.grade else 'none'} "
            f"avail={g.availability.value.type if g.availability else 'none'} "
            f"skills={len(g.skills)} conflicts={len(g.conflicts)}"
        )

    try:
        quality.assert_clean()  # LN-4: any leak block fails the run
    except RuntimeError as exc:
        typer.echo(f"  {exc}", err=True)
        raise typer.Exit(1) from None

    if parse_errors or silver_errors:
        raise typer.Exit(1)
    typer.echo("")


def index(
    gold_dir: Annotated[Path, typer.Option("--gold-dir")] = _GOLD_DEFAULT,
    db_path: Annotated[str, typer.Option("--db-path")] = "",
    run_id: Annotated[str, typer.Option("--run-id")] = "",
) -> None:
    """Build PII-free index records from gold, embed (passage), and upsert to Milvus Lite (a-005).

    Write-time only: re-embeds a candidate solely when its ``(gold_hash, model_version)`` changed
    (AD-082). No ``DSM_CANDIDATE_ID_KEY`` and no bronze read — ``embed_text`` is PII-free by
    construction (AD-084), so no per-candidate identity is needed.
    """
    import uuid

    from dsm.index.build import is_indexable
    from dsm.index.indexer import index_gold
    from dsm.index.milvus_store import MilvusIndexStore
    from dsm.ingest.goldstore import list_gold_ids, read_gold

    config = load_config()
    milvus_cfg = config["index"]["milvus"]
    model_version = config["models"]["embedder"]  # the embedder id — gates re-embed (AD-082)
    resolved_db = db_path or milvus_cfg["db_path"]
    rid = run_id or f"run-{uuid.uuid4().hex[:8]}"

    store = MilvusIndexStore(
        resolved_db, milvus_cfg["collection"], dim=768, metric=milvus_cfg["dense_metric"]
    )
    store.ensure_collection()

    # Read gold once; back the indexer's reader with it and reuse it for the PII-safe row lines.
    ids = sorted(list_gold_ids(gold_dir))
    golds = {cid: read_gold(cid, gold_root=gold_dir) for cid in ids}

    metrics = index_gold(
        ids,
        read_gold=golds.get,
        store=store,
        embed_client=_build_embed_client(),
        model_version=model_version,
        run_id=rid,
    )

    typer.echo(f"\n── Index ── run_id={rid}")
    typer.echo(f"  indexed           : {metrics.indexed}")
    typer.echo(f"  skipped-unchanged : {metrics.skipped_unchanged}")
    typer.echo(f"  tombstoned-removed: {metrics.tombstoned_removed}")
    typer.echo(f"  thin-skipped      : {metrics.thin_skipped}")
    if not ids:
        typer.echo(f"  (no gold found under {gold_dir})")
    for cid in ids:  # PII-safe per-entity line: candidate_id token + structured fields only
        g = golds[cid]
        if g is None:
            continue
        typer.echo(
            f"    {cid} grade={g.grade.value.value if g.grade else 'none'} "
            f"avail={g.availability.value.type if g.availability else 'none'} "
            f"skills={len(g.skills)} indexable={is_indexable(g)} tombstoned={g.is_tombstoned}"
        )
    typer.echo("")
