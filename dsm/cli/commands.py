"""CLI command implementations and the match orchestrator."""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import structlog
import typer

if TYPE_CHECKING:
    from dsm.pii.vault import Vault

from dsm.cli.store import GoldCandidateStore
from dsm.config import load_config
from dsm.index.embed_client import EmbedClient
from dsm.index.milvus_store import MilvusIndexStore
from dsm.index.retrieve import (
    exact_hard_skill_filter,
    hard_skill_gap,
    hybrid_recall,
    rerank,
)
from dsm.index.text_builder import build_role_query_passage
from dsm.match.clarify import clarify_role
from dsm.match.demand import parse_demand
from dsm.match.freshness import REFUSE, WARN, FreshnessVerdict, check_freshness
from dsm.match.gates import effective_free_date, filter_candidates
from dsm.match.intake import (
    ClarificationNeeded,
    IntakeCache,
    IntakePredictor,
    assemble_role,
    intake_cache_key,
)
from dsm.match.models import RoleIntake, ScoreExtraction
from dsm.match.rank import rank_assessments
from dsm.match.score import (
    NearMissRationalePredictor,
    ScorePredictor,
    explain_near_misses,
    score_candidate,
)
from dsm.models import (
    Candidate,
    EligiblePool,
    Exclusion,
    ExclusionLog,
    ExclusionReason,
    NearMiss,
    NoMatchResult,
    OpenRole,
    ShortlistResult,
    SkillDepth,
    TargetProfileScorecard,
)

# Route logs to STDERR so the machine-readable JSON that `match`/`explain` write to STDOUT stays
# clean — structlog's unconfigured default is a PrintLogger to stdout, which would otherwise let a
# warning (e.g. a vault miss or a per-candidate skip) corrupt the CLI's parseable output.
structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
_log = structlog.get_logger("dsm.cli.commands")

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_RAW_DEFAULT = _DATA_DIR / "raw"
_BRONZE_DEFAULT = _DATA_DIR / "bronze"
_SILVER_DEFAULT = _DATA_DIR / "silver"
_GOLD_DEFAULT = _DATA_DIR / "gold"
_DEMAND_DEFAULT = _RAW_DEFAULT / "demand" / "open_roles.csv"


def build_near_misses(
    candidates: list[Candidate],
    scorecard: TargetProfileScorecard,
    exclusion_log: ExclusionLog,
) -> list[NearMiss]:
    """Build ordered near-misses from structured data (AD-063b/c; AD-099).

    A near-miss is a candidate **one fixable decision** from qualifying: it fails only a negotiable
    gate (availability or location) **and** clears the non-negotiable hard skills (AD-099). A
    candidate missing a hard skill is *not* a near-miss — hard skills are exact-match with no
    adjacency (AD-033/072), so no date or location change rescues them; they stay fully recorded in
    the ``exclusion_log`` (the transparency layer), just not surfaced here. This **supersedes
    AD-088** (which listed hard-skill misses as near-misses) and **AD-097** (which instead labelled
    the skill gap in ``gap_summary``).

    Candidates excluded by a gate were never skill-checked (the exact filter runs only on gate
    survivors), so the availability/location misses are re-run through ``exact_hard_skill_filter``
    (no adjacency AD-033, proficiency floor AD-072 — so the verdict can't drift from the real gate)
    and only those that clear are kept. Gaps are recomputed from the ``Candidate`` +
    ``TargetProfileScorecard`` objects — ``Exclusion.detail`` is never parsed (O-NM-4).

    Ordering (AD-063b): **availability** misses first (smallest overshoot in days), then
    **location** misses; each tie-broken by ``candidate_email``. The full ordered list is returned;
    the orchestrator applies the top-3 cap (AD-063d).

    Args:
        candidates: the original candidate set (to look up name/location/availability/skills).
        scorecard: the clarified role (provides the availability deadline + hard skills).
        exclusion_log: the gate exclusions to turn into near-misses.

    Returns:
        Near-misses sorted per AD-063(b); unbounded (caller caps).
    """
    deadline = scorecard.start_date + timedelta(days=scorecard.availability_window_days)
    by_email = {candidate.email: candidate for candidate in candidates}

    # Which availability/location misses also clear the hard skills (AD-099)? They were dropped by
    # a gate before the exact filter ran, so re-run the real filter and keep only the clearers as
    # near-misses — the rest are not "one decision away" and belong only in the exclusion log.
    pre_filter = [
        by_email[exclusion.candidate_email]
        for exclusion in exclusion_log.exclusions
        if exclusion.reason
        in (ExclusionReason.AVAILABILITY_MISMATCH, ExclusionReason.LOCATION_MISMATCH)
        and exclusion.candidate_email in by_email
    ]
    cleared_pool, _ = exact_hard_skill_filter(
        EligiblePool(candidates=pre_filter, scorecard_id=scorecard.role_id),
        scorecard.hard_depth_skills,
    )
    clears_skills = {candidate.email for candidate in cleared_pool.candidates}

    ranked: list[tuple[tuple[int, int, str], NearMiss]] = []
    for exclusion in exclusion_log.exclusions:
        candidate = by_email.get(exclusion.candidate_email)
        if candidate is None:
            continue  # exclusion without a matching candidate — skip defensively
        # AD-099: only availability/location misses that clear hard skills are near-misses;
        # hard-skill misses (and gate misses that also fail skills) are not.
        if candidate.email not in clears_skills:
            continue

        if exclusion.reason is ExclusionReason.AVAILABILITY_MISMATCH:
            free_date = effective_free_date(candidate.availability)
            overshoot = (free_date - deadline).days if free_date is not None else 0
            day_word = "day" if overshoot == 1 else "days"
            gap_summary = f"available {overshoot} {day_word} after deadline"
            sort_key = (0, overshoot, candidate.email)
        else:  # LOCATION_MISMATCH (HARD_SKILL_MISMATCH never reaches here — excluded above)
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


def _config_snapshot(config: dict[str, Any], freshness: FreshnessVerdict | None) -> dict[str, Any]:
    """Reproducibility snapshot embedded on every result (AD-064; extended for b-002 lineage)."""
    index_cfg = config.get("index", {})
    return {
        "top_k": int(config["ranking"]["top_k"]),
        "weights": config["weights"],
        "models": config["models"],
        "recall": index_cfg.get("recall", {}),
        "rerank": index_cfg.get("rerank", {}),
        "freshness": freshness.model_dump() if freshness is not None else None,
    }


def build_closest_on_skills(
    candidates: list[Candidate],
    scorecard: TargetProfileScorecard,
    exclusion_log: ExclusionLog,
) -> list[NearMiss]:
    """Build the 'closest on skills' list (AD-100) — the skill-axis mirror of near-misses.

    Candidates whose exclusion reason is ``HARD_SKILL_MISMATCH`` cleared **both** gates (the exact
    filter runs only on gate survivors) and failed only on hard skills. Surface them ranked by
    fewest hard-skill gaps (missing + below-floor) first, then ``candidate_email``. The gap comes
    from the shared ``hard_skill_gap`` helper — the same logic the real filter uses (AD-072/033, no
    adjacency) — so this can't drift; ``Exclusion.detail`` is never parsed (AD-063c). Returns the
    full ordered list (caller caps + rationale-annotates). Disjoint from ``build_near_misses`` by
    construction (each candidate carries exactly one exclusion reason).
    """
    by_email = {candidate.email: candidate for candidate in candidates}
    ranked: list[tuple[tuple[int, str], NearMiss]] = []
    for exclusion in exclusion_log.exclusions:
        if exclusion.reason is not ExclusionReason.HARD_SKILL_MISMATCH:
            continue
        candidate = by_email.get(exclusion.candidate_email)
        if candidate is None:
            continue  # exclusion without a matching candidate — skip defensively
        gap = hard_skill_gap(candidate, scorecard.hard_depth_skills)
        if gap is None:
            continue  # defensive — a HARD_SKILL_MISMATCH always has a gap
        parts: list[str] = []
        if gap.missing:
            skill_word = "skill" if len(gap.missing) == 1 else "skills"
            parts.append(f"missing {len(gap.missing)} hard {skill_word}: {', '.join(gap.missing)}")
        if gap.below_floor:
            parts.append("below required proficiency: " + ", ".join(gap.below_floor))
        ranked.append(
            (
                (gap.count, candidate.email),  # fewest gaps first, then email (AD-100)
                NearMiss(
                    candidate_email=candidate.email,
                    name=candidate.name,
                    reason=exclusion.reason.value,
                    gap_summary="; ".join(parts),
                ),
            )
        )

    ranked.sort(key=lambda item: item[0])
    return [near_miss for _, near_miss in ranked]


def _no_match(
    scorecard: TargetProfileScorecard,
    candidates: list[Candidate],
    exclusions: list[Exclusion],
    reason: str,
    near_miss_predict: NearMissRationalePredictor | None = None,
) -> NoMatchResult:
    """Assemble a ``NoMatchResult``: top-3 near-misses + closest-on-skills (AD-063/097/098).

    ``near_misses`` (AD-099): clears hard skills, one negotiable gate away. ``closest_on_skills``
    (AD-100): cleared both gates, only a hard skill short. When a rationale predictor is injected,
    attach an LLM ``selection_rationale`` (AD-098) to the shown ≤3 of **each** list — after the
    cap, so we never explain entries beyond the top-3.
    """
    log = ExclusionLog(exclusions=exclusions)
    near = build_near_misses(candidates, scorecard, log)[:3]
    closest = build_closest_on_skills(candidates, scorecard, log)[:3]
    if near_miss_predict is not None:
        by_email = {candidate.email: candidate for candidate in candidates}
        near = explain_near_misses(near, by_email, scorecard, near_miss_predict)
        closest = explain_near_misses(closest, by_email, scorecard, near_miss_predict)
    return NoMatchResult(
        role_id=scorecard.role_id,
        reason=reason,
        near_misses=near,
        closest_on_skills=closest,
        exclusion_log=log,
    )


def _retrieve_and_rerank(
    candidates: list[Candidate],
    scorecard: TargetProfileScorecard,
    store: MilvusIndexStore | None,
    embed_client: EmbedClient | None,
    config: dict[str, Any],
) -> list[Candidate]:
    """Recall (OFF→passthrough) + rerank → candidates in scored order (§6.6/§6.7).

    When the retrieval deps aren't injected (``store``/``embed_client`` ``None`` — the pure-unit
    path), score the exact-filtered pool directly in input order. Otherwise build the role query
    passage, run hybrid recall (a no-op passthrough while ``index.recall.enabled`` is false),
    rerank to ``index.rerank.top_k``, and resolve the reranked ids back to the hydrated candidates.
    """
    if store is None or embed_client is None:
        return candidates
    role_query = build_role_query_passage(scorecard)
    retrieved = hybrid_recall(candidates, role_query, store, embed_client, config)
    top_k = int(config["index"]["rerank"]["top_k"])
    reranked = rerank(role_query, retrieved, store, embed_client, top_k=top_k)
    by_id = {candidate.email: candidate for candidate in candidates}
    return [by_id[r.candidate_id] for r in reranked if r.candidate_id in by_id]


def run_match(
    candidates: list[Candidate],
    scorecard: TargetProfileScorecard,
    *,
    store: MilvusIndexStore | None = None,
    embed_client: EmbedClient | None = None,
    score_predict: ScorePredictor,
    config: dict[str, Any],
    freshness: FreshnessVerdict | None = None,
    near_miss_predict: NearMissRationalePredictor | None = None,
) -> ShortlistResult | NoMatchResult:
    """The query-time spine (§4/§10): gate → exact filter → recall → rerank → score → rank.

    Parse / clarify / freshness happen at the command edge (``match``); this is the per-role
    pipeline over already-hydrated candidates. An empty pool at any narrowing stage (gate, exact
    filter) yields a ``NoMatchResult`` with ordered, capped near-misses (AD-063); otherwise the
    scored survivors are ranked. Retrieval deps are injected — ``store``/``embed_client`` ``None``
    skips recall+rerank (the pure-unit path); the LLM score seam (``score_predict``) and ``config``
    are always required.

    Args:
        candidates: the hydrated serving pool (``email`` carries the pseudonymised candidate_id).
        scorecard: the clarified role.
        store: the Milvus read store for recall/rerank, or ``None`` to skip them.
        embed_client: the embed/rerank client, or ``None`` to skip recall/rerank.
        score_predict: the injected LLM score seam (§6.8).
        config: runtime config (weights, ranking.top_k, index.recall/rerank, adjacency_map).
        freshness: the run's verdict; a ``warn`` is threaded into scoring to flag every assessment.
        near_miss_predict: optional LLM seam for the no-match path — attaches a
            ``selection_rationale`` to the shown near-misses (AD-098); ``None`` leaves them bare.

    Returns:
        A ``ShortlistResult`` (≥1 scored candidate) or a ``NoMatchResult`` (empty post-gate pool).
    """
    eligible_pool, gate_exclusions = filter_candidates(candidates, scorecard)
    if not eligible_pool.candidates:
        return _no_match(
            scorecard,
            candidates,
            gate_exclusions.exclusions,
            "No candidates passed the eligibility gates.",
            near_miss_predict,
        )

    filtered_pool, hard_exclusions = exact_hard_skill_filter(
        eligible_pool, scorecard.hard_depth_skills
    )
    all_exclusions = gate_exclusions.exclusions + hard_exclusions
    if not filtered_pool.candidates:
        return _no_match(
            scorecard,
            candidates,
            all_exclusions,
            "No candidates cleared the hard-skill filter.",
            near_miss_predict,
        )

    ordered = _retrieve_and_rerank(
        filtered_pool.candidates, scorecard, store, embed_client, config
    )
    assessments = [
        assessment
        for candidate in ordered
        if (
            assessment := score_candidate(
                candidate, scorecard, predict=score_predict, config=config, freshness=freshness
            )
        )
        is not None
    ]
    return rank_assessments(
        assessments,
        scorecard.role_id,
        ExclusionLog(exclusions=all_exclusions),
        int(config["ranking"]["top_k"]),
        _config_snapshot(config, freshness),
    )


def _build_clarify_predictor(config: dict[str, Any]):  # pragma: no cover - exercised only live
    """Build the live clarify predictor over PseudonymisedLM (monkeypatched in CLI tests)."""
    from dsm.match.clarify import make_clarify_predictor
    from dsm.pii.pseudonymised_lm import PseudonymisedLM

    return make_clarify_predictor(PseudonymisedLM(model=config["models"]["reasoning_llm"]))


def _build_intake_predictor(config: dict[str, Any]) -> IntakePredictor:  # pragma: no cover - live
    """Build the live NL-intake predictor over PseudonymisedLM (monkeypatched in CLI tests).

    Pass-through (invoked without ``pii_context``) — role text is non-PII (§7). Temperature is
    pinned to ``nl_intake.temperature`` **explicitly** (FR-1-AC-2): unlike the clarify/score
    builders (which rely on the dspy default), the intake parse must be deterministic.
    """
    from dsm.match.intake import make_intake_predictor
    from dsm.pii.pseudonymised_lm import PseudonymisedLM

    return make_intake_predictor(
        PseudonymisedLM(
            model=config["models"]["reasoning_llm"],
            temperature=config["nl_intake"]["temperature"],
        )
    )


class FileIntakeCache:
    """File-backed NL-parse cache (c-006, FR-6): one JSON per content-hash key under ``cache_dir``.

    Prose is non-PII (§7) and ``cache_dir`` lands under the already-gitignored ``data/.cache/*``,
    so nothing is committed. A corrupt/unreadable entry is treated as a miss; a write failure is
    logged and swallowed (a cache is best-effort, never load-bearing for correctness).
    """

    def __init__(self, cache_dir: Path) -> None:
        self._dir = cache_dir

    def get(self, key: str) -> RoleIntake | None:
        path = self._dir / f"{key}.json"
        if not path.is_file():
            return None
        try:
            return RoleIntake.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _log.warning("intake.cache_unreadable", key=key)
            return None

    def put(self, key: str, value: RoleIntake) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            (self._dir / f"{key}.json").write_text(value.model_dump_json(), encoding="utf-8")
        except OSError:
            _log.warning("intake.cache_write_failed", key=key)


def _build_intake_cache(
    config: dict[str, Any],
) -> IntakeCache:  # pragma: no cover - patched in tests
    """Open the file-backed NL-parse cache rooted at ``nl_intake.cache_dir`` (repo-relative)."""
    return FileIntakeCache(_DATA_DIR.parent / config["nl_intake"]["cache_dir"])


def _build_score_predictor(config: dict[str, Any]) -> ScorePredictor:  # pragma: no cover - live
    """Build the live score predictor over PseudonymisedLM (monkeypatched in CLI tests)."""
    from dsm.match.score import make_score_predictor
    from dsm.pii.pseudonymised_lm import PseudonymisedLM

    ner = None if config.get("pii", {}).get("ner_enabled", True) else (lambda _t: [])
    return make_score_predictor(PseudonymisedLM(model=config["models"]["reasoning_llm"], ner=ner))


def _pii_aware_score_predictor(base: ScorePredictor, vault: Vault) -> ScorePredictor:
    """Wrap the score predictor so each candidate's known identity is redacted before the LLM call.

    AD-101: the serving ``Candidate`` carries **de-anonymised** gold free-text (``feedback`` /
    ``profile_summary``), so before scoring we resolve that candidate's name/email from the vault
    (keyed by ``candidate_id``, which ``Candidate.email`` carries — AD-091) and enter
    :func:`pii_context`, so ``PseudonymisedLM`` strips them deterministically (+ NER residual) and
    leak-scans the outbound text. This wiring lives at the CLI composition root so ``dsm.match``
    never imports ``dsm.pii`` (R-11).

    **Vault miss is loud (not silent).** A missing entry yields an empty known list, so the
    deterministic strip + leak-scan have nothing to match on and protection falls back to NER alone
    — which itself degrades to a no-op when the spaCy model is absent. That combination would send
    candidate's de-anonymised gold free-text to the provider unredacted, so a miss is logged as a
    ``WARNING`` (PII-safe: ``candidate_id`` only) rather than passing quietly. (Fail-*closed* on a
    miss is a separate boundary-behaviour decision, deferred.)
    """
    from dsm.pii.pseudonymised_lm import pii_context

    def wrapped(scorecard: TargetProfileScorecard, candidate: Candidate) -> ScoreExtraction:
        identity = vault.get_identity(candidate.email)
        if identity is None:
            _log.warning("score.vault_miss_reduced_redaction", candidate_id=candidate.email)
        known_pii = [p for p in (identity or ()) if p]
        with pii_context(known_pii):
            return base(scorecard, candidate)

    return wrapped


def _build_near_miss_rationale_predictor(  # pragma: no cover - exercised only live
    config: dict[str, Any],
) -> NearMissRationalePredictor:
    """Build the live near-miss rationale predictor over PseudonymisedLM (live; tests patch)."""
    from dsm.match.score import make_near_miss_rationale_predictor
    from dsm.pii.pseudonymised_lm import PseudonymisedLM

    return make_near_miss_rationale_predictor(
        PseudonymisedLM(model=config["models"]["reasoning_llm"])
    )


def _build_query_store(
    config: dict[str, Any], db_path: str = ""
) -> MilvusIndexStore:  # pragma: no cover - live
    """Open the read-side Milvus store for recall/rerank (monkeypatched in CLI tests)."""
    milvus = config["index"]["milvus"]
    store = MilvusIndexStore(
        db_path or milvus["db_path"], milvus["collection"], dim=768, metric=milvus["dense_metric"]
    )
    store.ensure_collection()
    return store


def _freshness_for(
    demand_as_of: Any, start_date: Any, store: GoldCandidateStore, config: dict[str, Any]
) -> FreshnessVerdict | None:
    """Evaluate the freshness guard against the latest supply ``valid_as_of`` (None if undated)."""
    supply_as_of = store.latest_valid_as_of()
    if supply_as_of is None:
        return None  # no dated supply — an empty/undated pool resolves to no-match downstream
    return check_freshness(
        demand_as_of, supply_as_of, start_date, config["reconcile"]["max_staleness_days"]
    )


def _run_role(
    role: OpenRole,
    demand_as_of: date,
    *,
    clarify_predict: Any,
    gold_dir: Path,
    db_path: str,
    vault_path: Path | None,
) -> tuple[ShortlistResult | NoMatchResult, Vault]:
    """Drive the query-time spine for one already-built ``OpenRole`` (shared by both front doors).

    hydrate → freshness guard → clarify → ``run_match``. ``refuse`` blocks the run
    (``typer.Exit(1)``); ``warn`` is threaded into scoring. The ``clarify_predict`` seam is the
    only difference between the doors: the **CSV** door passes the live clarify predictor (refine
    from the Notes cell); the **NL** door passes ``None`` so ``clarify_role`` takes its echo path —
    the intake LLM already interpreted the free text, so exactly one LLM interpretation runs
    (AD-XXX). ``demand_as_of`` is the CSV banner date for the CSV door and the run-date for NL.

    Returns the **pseudonymised** pipeline result (identity = ``candidate_id``, AD-091) **and** the
    vault, so the caller can run :func:`render_identities` at the output edge (AD-107). Keeping the
    pipeline output pseudonymised leaves the determinism invariant + eval cassettes untouched.
    """
    config = load_config()
    store = GoldCandidateStore(gold_dir)
    candidates = store.get(store.all_ids())

    verdict = _freshness_for(demand_as_of, role.start_date, store, config)
    if verdict is not None and verdict.action == REFUSE:
        typer.echo(verdict.message, err=True)
        raise typer.Exit(1)

    # clarify reads role text only (no candidate PII, §7) → predictor left unwrapped, pass-through.
    scorecard = clarify_role(role, predict=clarify_predict)
    # score sees the candidate's de-anonymised gold free-text → wrap the predictor so each call
    # runs under pii_context from the vault (AD-101). Read path mirrors the ingest write.
    from dsm.pii.vault import FileVault

    vault = FileVault(vault_path or gold_dir.parent / "identity" / "vault.json")
    result = run_match(
        candidates,
        scorecard,
        store=_build_query_store(config, db_path),
        embed_client=_build_embed_client(),
        score_predict=_pii_aware_score_predictor(_build_score_predictor(config), vault),
        config=config,
        freshness=verdict if verdict is not None and verdict.action == WARN else None,
        near_miss_predict=_build_near_miss_rationale_predictor(config),
    )
    return result, vault


def _match_role(
    role_id: str, csv_path: Path, gold_dir: Path, db_path: str, vault_path: Path | None = None
) -> tuple[ShortlistResult | NoMatchResult, Vault]:
    """CSV front door: parse demand → select ``role_id`` → :func:`_run_role` (explain uses it too).

    ``demand_as_of`` is the Open Roles CSV banner date (AD-087). Single role per invocation
    (AD-050). The live clarify predictor is built here and threaded into the shared spine.
    """
    config = load_config()
    try:
        outcome = parse_demand(csv_path)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Cannot parse demand CSV: {exc}", err=True)
        raise typer.Exit(1) from None

    role = next((r for r in outcome.roles if r.role_id == role_id), None)
    if role is None:
        typer.echo(f"Role {role_id!r} not found in {csv_path}", err=True)
        raise typer.Exit(1)

    return _run_role(
        role,
        outcome.banner.demand_as_of,
        clarify_predict=_build_clarify_predictor(config),
        gold_dir=gold_dir,
        db_path=db_path,
        vault_path=vault_path,
    )


def _echo_role(role: OpenRole) -> None:
    """Print the parsed ``OpenRole`` for human confirmation before any gate runs (FR-3).

    Lists every parser-populated, gating-relevant field — incl. the **Python-derived**
    ``co_location_required`` (FR-8) and the resolved ``start_date`` — so a misparse is caught
    before it gates. Fields the NL parser never populates (``onsite_cities``/``preferred_skills``)
    are flagged as not-from-prose, never implied as extracted.
    """
    loc = role.location
    where = loc.city or ("remote (India)" if loc.remote_within_country else "—")
    hard = [
        s.name + (f" ({s.min_proficiency.value})" if s.min_proficiency else "")
        for s in role.required_skills
        if s.depth == SkillDepth.HARD
    ]
    desired = [s.name for s in role.required_skills if s.depth == SkillDepth.DESIRED]
    onsite = "required (onsite)" if role.co_location_required else "not required"
    # To stderr so the machine-readable shortlist JSON on stdout stays clean (mirrors the freshness
    # / error messages + the structlog→stderr routing).
    typer.echo("\n── Parsed role ──", err=True)
    typer.echo(f"  title          : {role.title or '—'}", err=True)
    typer.echo(f"  location       : {where}", err=True)
    typer.echo(f"  co-location    : {onsite}", err=True)
    typer.echo(f"  start date     : {role.start_date.isoformat()}", err=True)
    typer.echo(f"  hard skills    : {', '.join(hard) or '—'}", err=True)
    typer.echo(f"  desired skills : {', '.join(desired) or '—'}", err=True)
    if role.description:
        typer.echo(f"  notes          : {role.description}", err=True)
    typer.echo("  (onsite_cities / preferred_skills are not parsed from prose)", err=True)


def _clarify_missing(assembly: ClarificationNeeded, today: date) -> RoleIntake:
    """One bounded Python clarification round — one prompt per missing gate field, NO LLM (FR-4).

    Parses the operator's typed answers deterministically (a city string, or an ISO date) and
    returns an updated ``RoleIntake`` for a single re-assembly. The LLM is never re-invoked.
    """
    updates: dict[str, Any] = {}
    for field in assembly.missing:
        if field == "location":
            answer = typer.prompt(
                "Which city is the role in? (or type 'remote')", err=True
            ).strip()
            if answer.lower() == "remote":
                updates["location_city"] = None
                updates["remote_within_country"] = True
            else:
                updates["location_city"] = answer
        else:  # "start"
            answer = typer.prompt("What is the start date? (ISO YYYY-MM-DD)", err=True).strip()
            updates["start_date_iso"] = answer
            updates["start_date_phrase"] = answer
    return assembly.partial.model_copy(update=updates)


def _match_query(
    prose: str,
    gold_dir: Path,
    db_path: str,
    vault_path: Path | None = None,
    *,
    yes: bool = False,
) -> tuple[ShortlistResult | NoMatchResult, Vault]:
    """NL front door: parse prose → assemble/validate → echo+confirm → :func:`_run_role` (AD-XXX).

    The intake LLM (single-shot, via ``PseudonymisedLM`` pass-through) parses the prose into a
    typed ``RoleIntake``; **Python** assembles + validates it into the existing ``OpenRole``
    (deriving ``co_location_required``, validating the resolved date). A missing required field
    triggers **one** bounded Python clarification round (no LLM). The parsed role is **always
    echoed**; gating proceeds only after confirmation (or ``--yes``). ``demand_as_of`` is the
    run-date (so freshness is ``ok``/``refuse`` only — AD-XXY). The parse is content-hash cached.
    """
    config = load_config()
    today = date.today()
    nl_cfg = config["nl_intake"]
    model_id = config["models"]["reasoning_llm"]
    key = intake_cache_key(prose, today, model_id, nl_cfg["prompt_version"])

    cache = _build_intake_cache(config)
    intake = cache.get(key)
    if intake is None:
        predict = _build_intake_predictor(config)
        try:
            intake = predict(prose, today)
        except Exception as exc:  # noqa: BLE001 — no parse ⇒ no OpenRole to fall back to (≠ clarify)
            _log.warning("intake.parse_failed", reason=type(exc).__name__)
            typer.echo(f"Could not parse the query ({type(exc).__name__}).", err=True)
            raise typer.Exit(1) from None
        cache.put(key, intake)

    role_id = f"NL-{key[:8]}"
    max_horizon = int(nl_cfg["max_horizon_days"])
    assembly = assemble_role(intake, today, max_horizon_days=max_horizon, role_id=role_id)
    if isinstance(assembly, ClarificationNeeded):
        updated = _clarify_missing(assembly, today)  # single round, pure Python, no LLM (FR-4)
        assembly = assemble_role(updated, today, max_horizon_days=max_horizon, role_id=role_id)
        if isinstance(assembly, ClarificationNeeded):
            typer.echo(
                f"Still missing required field(s): {', '.join(assembly.missing)}. Aborting.",
                err=True,
            )
            raise typer.Exit(1)

    role = assembly
    _echo_role(role)  # always shown, even under --yes (audit)
    if not (yes or typer.confirm("Proceed with this role?", err=True)):
        typer.echo("Cancelled — no shortlist produced.", err=True)
        raise typer.Exit(1)

    return _run_role(
        role,
        today,
        clarify_predict=None,  # NL: intake already interpreted the free text → echo path (AD-XXX)
        gold_dir=gold_dir,
        db_path=db_path,
        vault_path=vault_path,
    )


def render_identities(
    result: ShortlistResult | NoMatchResult, vault: Vault
) -> ShortlistResult | NoMatchResult:
    """Substitute real name/email from the vault into the output — the final render step (AD-107).

    The query pipeline emits **pseudonymised** results: every identity field carries the
    ``candidate_id`` (AD-091). This is the deferred human-facing render — at the CLI composition
    root (so ``dsm.match`` never imports ``dsm.pii``), resolve each ``candidate_id`` to its real
    ``(name, email)`` via the vault and rebuild the frozen output models with ``model_copy``.
    Covers **all** identity-bearing fields: ranked candidates, both ``NearMiss`` lists, and the
    full exclusion log. Returns copies; the input is never mutated.

    A vault miss keeps the ``candidate_id`` and logs a PII-safe ``WARNING`` (``candidate_id``
    only), never fail-closed — consistent with AD-103. This governs **output** only; the
    ``no-PII-leak`` invariant covers provider inputs + narratives, not these fields (no relax).
    """
    cache: dict[str, tuple[str, str] | None] = {}

    def identity(cid: str) -> tuple[str, str] | None:
        if cid not in cache:
            value = vault.get_identity(cid)
            if value is None:
                _log.warning("render.vault_miss_identity", candidate_id=cid)
            cache[cid] = value
        return cache[cid]

    def reveal_candidate(candidate: Candidate) -> Candidate:
        ident = identity(candidate.email)
        if ident is None:
            return candidate
        name, email = ident
        return candidate.model_copy(update={"email": email, "name": name})

    def reveal_near_miss(nm: NearMiss) -> NearMiss:
        ident = identity(nm.candidate_email)
        if ident is None:
            return nm
        name, email = ident
        return nm.model_copy(update={"candidate_email": email, "name": name})

    def reveal_exclusions(log: ExclusionLog) -> ExclusionLog:
        revealed: list[Exclusion] = []
        for exc in log.exclusions:
            ident = identity(exc.candidate_email)
            if ident is None:
                revealed.append(exc)
            else:
                _name, email = ident
                revealed.append(exc.model_copy(update={"candidate_email": email}))
        return ExclusionLog(exclusions=revealed)

    if isinstance(result, ShortlistResult):
        return result.model_copy(
            update={
                "ranked_assessments": [
                    a.model_copy(update={"candidate": reveal_candidate(a.candidate)})
                    for a in result.ranked_assessments
                ],
                "exclusion_log": reveal_exclusions(result.exclusion_log),
            }
        )
    return result.model_copy(
        update={
            "near_misses": [reveal_near_miss(nm) for nm in result.near_misses],
            "closest_on_skills": [reveal_near_miss(nm) for nm in result.closest_on_skills],
            "exclusion_log": reveal_exclusions(result.exclusion_log),
        }
    )


def _exclusion_lines(result: ShortlistResult | NoMatchResult) -> list[dict[str, str]]:
    """The gate/exact-filter outcomes (who was dropped and why) for the lineage dump."""
    return [
        {"candidate": e.candidate_email, "reason": e.reason.value, "detail": e.detail}
        for e in result.exclusion_log.exclusions
    ]


def _lineage(result: ShortlistResult | NoMatchResult) -> dict[str, Any]:
    """Build the explain lineage from what the result already carries (§9; no new persistence)."""
    if isinstance(result, NoMatchResult):
        return {
            "role_id": result.role_id,
            "outcome": "no_match",
            "reason": result.reason,
            "exclusions": _exclusion_lines(result),
            "near_misses": [
                {
                    "candidate": nm.candidate_email,
                    "reason": nm.reason,
                    "gap": nm.gap_summary,
                    "rationale": nm.selection_rationale,
                }
                for nm in result.near_misses
            ],
            "closest_on_skills": [
                {
                    "candidate": nm.candidate_email,
                    "reason": nm.reason,
                    "gap": nm.gap_summary,
                    "rationale": nm.selection_rationale,
                }
                for nm in result.closest_on_skills
            ],
        }

    snapshot = result.config_snapshot
    recall_enabled = bool(snapshot.get("recall", {}).get("enabled"))
    return {
        "role_id": result.role_id,
        "outcome": "shortlist",
        "total_eligible": result.total_eligible,
        "recall_mode": "hybrid" if recall_enabled else "exhaustive",
        "freshness": snapshot.get("freshness"),
        "config_snapshot": snapshot,
        "exclusions": _exclusion_lines(result),
        "shortlist": [
            {
                "candidate": a.candidate.email,
                "combined_score": a.combined_score,
                "skill_match_score": a.skill_match_score,
                "feedback_score": a.feedback_score,
                "hard_skill_coverage": a.hard_skill_coverage,
                "desired_skill_coverage": a.desired_skill_coverage,
                "flags": [{"type": f.type.value, "message": f.message} for f in a.flags],
                "evidence": [{"source": c.source.value, "text": c.text} for c in a.evidence],
                "narrative": a.narrative,
            }
            for a in result.ranked_assessments
        ],
    }


def match(
    role_id: Annotated[str | None, typer.Option("--role-id")] = None,
    query: Annotated[str | None, typer.Option("--query")] = None,
    csv_path: Annotated[Path, typer.Option("--csv-path")] = _DEMAND_DEFAULT,
    gold_dir: Annotated[Path, typer.Option("--gold-dir")] = _GOLD_DEFAULT,
    db_path: Annotated[str, typer.Option("--db-path")] = "",
    vault_path: Annotated[Path | None, typer.Option("--vault-path")] = None,
    yes: Annotated[bool, typer.Option("--yes")] = False,
) -> None:
    """Match one open role to a ranked shortlist (§4) — from the demand CSV or free-text prose.

    Provide **exactly one** of ``--role-id`` (parsed from the demand CSV) or ``--query`` (free-text
    prose). The prose door parses the request into the **same** typed ``OpenRole`` the CSV makes,
    echoes it, and — unless ``--yes`` — asks for confirmation before gating (AD-XXX). Then the full
    unchanged spine runs: freshness guard → clarify → gate → exact filter → recall → rerank → score
    → rank. ``refuse`` freshness blocks the run; ``warn`` flags every assessment. Single role per
    invocation (AD-050). Prints a ``ShortlistResult`` / ``NoMatchResult`` JSON with **real**
    candidate name/email rendered from the vault (AD-107).
    """
    if (role_id is None) == (query is None):
        typer.echo("Provide exactly one of --role-id or --query.", err=True)
        raise typer.Exit(1)
    if query is not None:
        result, vault = _match_query(query, gold_dir, db_path, vault_path, yes=yes)
    else:
        assert role_id is not None  # narrowed by the exactly-one check above
        result, vault = _match_role(role_id, csv_path, gold_dir, db_path, vault_path)
    result = render_identities(result, vault)
    typer.echo(result.model_dump_json(indent=2))


def explain(
    role_id: Annotated[str, typer.Option("--role-id")],
    csv_path: Annotated[Path, typer.Option("--csv-path")] = _DEMAND_DEFAULT,
    gold_dir: Annotated[Path, typer.Option("--gold-dir")] = _GOLD_DEFAULT,
    db_path: Annotated[str, typer.Option("--db-path")] = "",
    vault_path: Annotated[Path | None, typer.Option("--vault-path")] = None,
) -> None:
    """Re-run the pipeline for ``--role-id`` and dump its full lineage (§9).

    Reads only what ``ShortlistResult`` / ``NoMatchResult`` already carry — freshness verdict,
    gate + exact-filter outcomes, recall mode, sub-scores, citations, ``config_snapshot``
    (shortlist); or the reason + ordered near-misses (no-match). No new persistence layer.
    Candidate name/email are rendered real from the vault (AD-107) before the lineage is built.
    """
    result, vault = _match_role(role_id, csv_path, gold_dir, db_path, vault_path)
    result = render_identities(result, vault)
    typer.echo(json.dumps(_lineage(result), indent=2))


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
    vault_path: Annotated[Path | None, typer.Option("--vault-path")] = None,
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
    from dsm.ingest.reconcile import freshness_guard, reconcile, revive, tombstone
    from dsm.pii.leakscan import PIILeakError
    from dsm.pii.vault import FileVault
    from dsm.pii.vault import candidate_id as derive_cid

    config = load_config()
    # Persistent identity vault (AD-102): ingest WRITES here; a later `dsm match` process READS it
    # to drive the query-time deterministic redact pass (AD-101). Defaults alongside gold under the
    # same (gitignored) data root, so it follows --gold-dir in tests + prod.
    vault = FileVault(vault_path or gold_dir.parent / "identity" / "vault.json")
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

    # Reconcile against the FULL current supply roster — every candidate_id present in the supply
    # CSVs as they exist now (LANDED *or* SKIPPED this run), not just the files re-landed this run.
    # The candidate universe is the supply sheets (AD-013); landing's content-hash skip is a
    # blob-write optimisation and must not shrink the snapshot reconcile diffs against — otherwise
    # editing one supply file tombstones every candidate whose (unchanged) file was SKIPPED at
    # landing (AD-093). Re-parsing the supply blobs is deterministic + LLM-free, so it is cheap.
    current_ids: set[str] = {g.candidate_id for g in gold}  # defensive: never tombstone what we
    for entry in entries:  # just wrote this run
        if (
            entry.source_type not in _SUPPLY_TYPES
            or entry.raw_bytes_hash is None
            or entry.status not in (LandingStatus.LANDED, LandingStatus.SKIPPED)
        ):
            continue
        assert entry.source_type is not None  # narrowed by the _SUPPLY_TYPES check above
        for supply_rec in parse_blob(
            blobs.get(entry.raw_bytes_hash),
            entry.source_type,
            entry.raw_bytes_hash,
            run_id=rid,
        ):
            email = _bronze_cell(supply_rec.raw, "Email")
            if email:
                current_ids.add(derive_cid(email))

    typer.echo("\n── Gold ──")
    if not current_ids:
        # No supply roster at all this run — a run that landed nothing or has no supply files. This
        # is NOT a current snapshot, so we must NOT tombstone prior gold (that would flag every
        # consultant as departed). Leave it untouched.
        typer.echo("  no supply roster this run — gold left unchanged (no reconcile)")
        typer.echo("  (re-run is idempotent; to rebuild gold, re-land changed inputs)")
        if parse_errors or silver_errors:
            raise typer.Exit(1)
        typer.echo("")
        return

    landed_ids = {g.candidate_id for g in gold}
    prior_ids = list_gold_ids(gold_dir)
    rec = reconcile(current_ids, prior_ids)
    # Revive: a candidate live in the current supply roster whose on-disk gold is tombstoned but
    # whose supply file was SKIPPED at landing (byte-identical to a prior version) is never
    # re-merged above. Flip them back to live — the tombstone preserved their content — so
    # re-adding a removed row revives them even when landing dedups the file (AD-094).
    revivable = [
        cid
        for cid in current_ids - landed_ids
        if (prior := read_gold(cid, gold_dir)) is not None and prior.is_tombstoned
    ]

    if not gold and not rec.tombstoned_ids and not revivable:
        # Pure idempotent re-run: every supply file SKIPPED, roster == prior, nobody departed or
        # revived. Nothing changed — leave gold untouched.
        typer.echo("  no changes this run — gold left unchanged")
        typer.echo("  (re-run is idempotent; to rebuild gold, re-land changed inputs)")
        if parse_errors or silver_errors:
            raise typer.Exit(1)
        typer.echo("")
        return

    for g in gold:
        write_gold(g, gold_dir)
    for cid in rec.tombstoned_ids:
        prior = read_gold(cid, gold_dir)
        if prior is not None:
            write_gold(tombstone(prior), gold_dir)
            log_tombstone(run_id=rid, candidate_id=cid)
    for cid in revivable:
        prior = read_gold(cid, gold_dir)
        if prior is not None:
            write_gold(revive(prior), gold_dir)
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
    typer.echo(f"  revived     : {len(revivable)}")
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
