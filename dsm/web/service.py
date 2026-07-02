"""Web service composition (c-008; AD-XXX) — the logic behind the FastAPI routes.

A composition root: it **reuses** the CLI builders + spine (``dsm.cli.commands``) and adds no
matching/scoring/eligibility logic. It never imports ``dsm.pii`` directly — the PII boundary lives
in the CLI builders it calls; de-anonymisation happens only at the output edge via
``render_identities`` (AD-107). The match response is a *view* that pairs the pseudonymous
``candidate_id`` (captured pre-render) with the de-anonymised identity (post-render) — a stable,
PII-safe handle for ``/resume`` + ``/decisions`` while showing real name/email, all without
touching the frozen ``dsm.models`` (AD-060).
"""

from __future__ import annotations

import json
import re
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

import dsm.cli.commands as commands
from dsm.cli.store import GoldCandidateStore
from dsm.config import load_config
from dsm.ingest.blobstore import LocalFSBlobStore
from dsm.ingest.goldstore import read_gold
from dsm.ingest.models import NormalizedRecord, SourceType
from dsm.match.demand import parse_demand
from dsm.match.freshness import REFUSE
from dsm.match.intake import ClarificationNeeded, assemble_role, intake_cache_key
from dsm.match.models import RoleIntake
from dsm.models import (
    EvidenceSource,
    FreeNow,
    Location,
    NewJoiner,
    NoMatchResult,
    OpenRole,
    RollingOff,
    ShortlistResult,
    SkillDepth,
)
from dsm.web.models import (
    AssessmentView,
    CandidateView,
    Clarifications,
    DecisionRequest,
    DecisionResponse,
    DemandParseResponse,
    EvidenceView,
    ExclusionView,
    FlagView,
    IntakeRequest,
    IntakeResponse,
    MatchResponse,
    NearMissView,
    RoleEcho,
    RoleSummary,
)

_GOLD_DEFAULT = commands._GOLD_DEFAULT
_BRONZE_DEFAULT = commands._BRONZE_DEFAULT
_DECISIONS_DEFAULT = commands._DATA_DIR / "decisions"
_RAW_DEFAULT = commands._RAW_DEFAULT  # c-011: supply CSVs + resumes + feedback
_JOBS_DEFAULT = commands._DATA_DIR / "ingest_jobs"  # c-011: ingest-job logs (gitignored)
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


# ---------------------------------------------------------------------------
# Typed service errors — each maps to its FR-8 HTTP status at the app edge
# ---------------------------------------------------------------------------


class WebServiceError(Exception):
    """Base for service errors. ``status_code`` + ``detail`` drive a single FastAPI handler.

    ``detail`` is the **only** thing surfaced to the client and is PII-safe (``candidate_id`` only,
    never name/email or a vault path), mirroring the CLI's stderr discipline (FR-8-AC-2).
    """

    status_code: int = 500

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class EmptyQuery(WebServiceError):
    status_code = 400


class IntakeFailed(WebServiceError):
    status_code = 400


class BadDemandCSV(WebServiceError):
    status_code = 400


class RoleNotFound(WebServiceError):
    status_code = 404


class ResumeNotFound(WebServiceError):
    status_code = 404


class ClarificationRequired(WebServiceError):
    status_code = 422


class FreshnessRefused(WebServiceError):
    status_code = 409


class NotConfirmed(WebServiceError):
    status_code = 400


# ---------------------------------------------------------------------------
# Small display helpers
# ---------------------------------------------------------------------------


def _location_str(location: Location) -> str:
    """Human-readable location for the echo / candidate card."""
    if location.city:
        return location.city
    if location.remote_within_country:
        return f"remote ({location.country})"
    return f"any ({location.country}, distributed)"


def _availability_str(availability: Any) -> str:
    """Short human string for the candidate's availability variant."""
    if isinstance(availability, FreeNow):
        return "free now"
    if isinstance(availability, RollingOff):
        return f"rolling off ~{availability.expected_date.isoformat()}"
    if isinstance(availability, NewJoiner):
        return f"joins {availability.join_date.isoformat()}"
    return "unknown"


def _role_echo(role: OpenRole, start_phrase: str | None) -> RoleEcho:
    """Build the confirmation echo from an assembled ``OpenRole`` (mirrors CLI ``_echo_role``)."""
    hard = [
        s.name + (f" ({s.min_proficiency.value})" if s.min_proficiency else "")
        for s in role.required_skills
        if s.depth == SkillDepth.HARD
    ]
    desired = [s.name for s in role.required_skills if s.depth == SkillDepth.DESIRED]
    return RoleEcho(
        role_id=role.role_id,
        title=role.title or "",
        location=_location_str(role.location),
        co_location_required=role.co_location_required,  # Python-derived, display only (AD-002)
        exclude_cities=sorted(role.exclude_cities),
        start_date=role.start_date.isoformat(),
        start_phrase=start_phrase,
        hard_skills=hard,
        desired_skills=desired,
        notes=role.description,
    )


# ---------------------------------------------------------------------------
# NL intake — parse + echo (/intake) and confirm + run (/match/query)
# ---------------------------------------------------------------------------


def _parse_intake(prose: str, today: date, config: dict[str, Any]) -> tuple[RoleIntake, str]:
    """Parse prose → ``RoleIntake`` via the cached single-shot predictor (cf. ``_match_query``).

    Cache hit ⇒ no LLM call (deterministic, content-hash keyed, AD-066). Builders come from the CLI
    composition root (monkeypatched in tests), so the PII boundary + cache wiring are shared.
    """
    nl_cfg = config["nl_intake"]
    model_id = config["models"]["reasoning_llm"]
    key = intake_cache_key(prose, today, model_id, nl_cfg["prompt_version"])
    cache = commands._build_intake_cache(config)
    intake = cache.get(key)
    if intake is None:
        predict = commands._build_intake_predictor(config)
        try:
            intake = predict(prose, today)
        except Exception as exc:  # noqa: BLE001 — no parse ⇒ no role; surface a clean 400
            raise IntakeFailed(f"Could not parse the query ({type(exc).__name__}).") from None
        cache.put(key, intake)
    return intake, key


def apply_clarifications(partial: RoleIntake, answers: Clarifications | None) -> RoleIntake:
    """Apply operator clarification answers in Python — never an LLM (cf. ``_clarify_missing``).

    ``location`` "remote" ⇒ distributed-remote; else ⇒ the city. ``start`` ⇒ the ISO date (also
    kept as the phrase for the echo). Absent answers leave the field untouched.
    """
    if answers is None:
        return partial
    updates: dict[str, Any] = {}
    if answers.location is not None:
        if answers.location.strip().lower() == "remote":
            updates["location_city"] = None
            updates["remote_within_country"] = True
        else:
            updates["location_city"] = answers.location.strip()
    if answers.start is not None:
        updates["start_date_iso"] = answers.start.strip()
        updates["start_date_phrase"] = answers.start.strip()
    return partial.model_copy(update=updates)


def intake_echo(
    prose: str, *, gold_dir: Path | None = None, config: dict[str, Any] | None = None
) -> IntakeResponse:
    """``POST /intake`` — parse prose and return the role echo (or the missing fields to clarify).

    Runs no gate. The eligibility boundary is the **confirmed** role at ``/match/query`` (AD-110).
    """
    if not prose.strip():
        raise EmptyQuery("Query is empty — provide a role description.")
    cfg = config or load_config()
    today = date.today()
    intake, key = _parse_intake(prose, today, cfg)
    role_id = f"NL-{key[:8]}"
    max_horizon = int(cfg["nl_intake"]["max_horizon_days"])
    assembly = assemble_role(intake, today, max_horizon_days=max_horizon, role_id=role_id)
    if isinstance(assembly, ClarificationNeeded):
        return IntakeResponse(
            status="needs_clarification", role_id=role_id, missing=list(assembly.missing)
        )
    return IntakeResponse(
        status="ready", role_id=role_id, echo=_role_echo(assembly, intake.start_date_phrase)
    )


def match_query(
    req: IntakeRequest,
    *,
    gold_dir: Path | None = None,
    db_path: str = "",
    vault_path: Path | None = None,
    config: dict[str, Any] | None = None,
) -> MatchResponse:
    """``POST /match/query`` — confirm + run the NL door (one LLM interpretation; AD-110)."""
    if not req.prose.strip():
        raise EmptyQuery("Query is empty — provide a role description.")
    if not req.confirm:
        # The confirmed role is the eligibility boundary (FR-3-AC-1, AD-110): a caller must echo
        # the parsed role back via /intake and explicitly confirm before any gate runs.
        raise NotConfirmed("Confirm the parsed role before matching (set confirm=true).")
    cfg = config or load_config()
    today = date.today()
    intake, key = _parse_intake(req.prose, today, cfg)  # cache hit after /intake — no second parse
    role_id = f"NL-{key[:8]}"
    max_horizon = int(cfg["nl_intake"]["max_horizon_days"])
    assembly = assemble_role(intake, today, max_horizon_days=max_horizon, role_id=role_id)
    if isinstance(assembly, ClarificationNeeded):
        clarified = apply_clarifications(assembly.partial, req.clarifications)
        assembly = assemble_role(clarified, today, max_horizon_days=max_horizon, role_id=role_id)
        if isinstance(assembly, ClarificationNeeded):
            raise ClarificationRequired(
                f"Still missing required field(s): {', '.join(assembly.missing)}."
            )
    return _run_role_view(
        assembly,
        today,  # NL: demand_as_of = run-date (freshness ok/refuse only, AD-111)
        clarify_predict=None,  # NL echo path — intake already interpreted the prose
        gold_dir=gold_dir or _GOLD_DEFAULT,
        db_path=db_path,
        vault_path=vault_path,
        config=cfg,
    )


# ---------------------------------------------------------------------------
# CSV / demand-sheet door — /demand/parse (picker) and /match/role (run)
# ---------------------------------------------------------------------------


def _parse_demand_bytes(csv_bytes: bytes):
    """Parse uploaded CSV bytes via the existing ``parse_demand`` (writes a temp file)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "open_roles.csv"
        path.write_bytes(csv_bytes)
        try:
            return parse_demand(path)
        except (FileNotFoundError, ValueError) as exc:
            raise BadDemandCSV(f"Cannot parse demand CSV: {exc}") from None


def demand_parse(csv_bytes: bytes) -> DemandParseResponse:
    """``POST /demand/parse`` — parse the upload into the role picker payload (no match yet)."""
    outcome = _parse_demand_bytes(csv_bytes)
    roles = [
        RoleSummary(
            role_id=r.role_id,
            title=r.title or "",
            location=_location_str(r.location),
            start_date=r.start_date.isoformat(),
            co_location_required=r.co_location_required,
        )
        for r in outcome.roles
    ]
    return DemandParseResponse(
        demand_as_of=outcome.banner.demand_as_of.isoformat(),
        roles=roles,
        skipped=list(outcome.skipped),
    )


def match_role(
    csv_bytes: bytes,
    role_id: str,
    *,
    gold_dir: Path | None = None,
    db_path: str = "",
    vault_path: Path | None = None,
    config: dict[str, Any] | None = None,
) -> MatchResponse:
    """``POST /match/role`` — select ``role_id`` from the upload and run the CSV door."""
    cfg = config or load_config()
    outcome = _parse_demand_bytes(csv_bytes)
    role = next((r for r in outcome.roles if r.role_id == role_id), None)
    if role is None:
        raise RoleNotFound(f"Role {role_id!r} not found in the uploaded CSV.")
    return _run_role_view(
        role,
        outcome.banner.demand_as_of,
        clarify_predict=commands._build_clarify_predictor(cfg),  # CSV door: live clarify
        gold_dir=gold_dir or _GOLD_DEFAULT,
        db_path=db_path,
        vault_path=vault_path,
        config=cfg,
    )


# ---------------------------------------------------------------------------
# Shared run + view assembly
# ---------------------------------------------------------------------------


def _run_role_view(
    role: OpenRole,
    demand_as_of: date,
    *,
    clarify_predict: Any,
    gold_dir: Path,
    db_path: str,
    vault_path: Path | None,
    config: dict[str, Any],
) -> MatchResponse:
    """Freshness pre-guard → reuse ``_run_role`` → ``render_identities`` → build the view.

    The freshness verdict is computed here so a ``refuse`` becomes a clean ``409`` (with the
    message) — ``_run_role`` then re-checks it and, on the non-refuse path, never reaches its
    ``typer.Exit`` branch. This reuses the entire PII-safe spine composition with no fork.
    """
    store = GoldCandidateStore(gold_dir)
    verdict = commands._freshness_for(demand_as_of, role.start_date, store, config)
    if verdict is not None and verdict.action == REFUSE:
        raise FreshnessRefused(verdict.message)
    pseudo, vault = commands._run_role(
        role,
        demand_as_of,
        clarify_predict=clarify_predict,
        gold_dir=gold_dir,
        db_path=db_path,
        vault_path=vault_path,
    )
    rendered = commands.render_identities(pseudo, vault)
    return _build_view(pseudo, rendered, role_id=role.role_id, gold_dir=gold_dir)


def _silver_resume_hashes(candidate_id: str, silver_dir: Path) -> list[str]:
    """Résumé bronze-blob hashes from the silver RESUME records for this candidate.

    The reliable candidate→résumé-blob link: a silver ``NormalizedRecord`` with
    ``source_type == RESUME`` carries the bronze ``source_hash`` (gold ``profile_pdf`` citations
    may not, depending on enrich/merge version). Reads only ``source_hash`` — never raw_text.
    """
    records_dir = silver_dir / "records"
    if not records_dir.is_dir():
        return []
    out: list[str] = []
    for path in sorted(records_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = NormalizedRecord.model_validate_json(line)
            except (ValueError, ValidationError):
                continue
            if (
                rec.candidate_id == candidate_id
                and rec.source_type is SourceType.RESUME
                and rec.source_hash
            ):
                out.append(rec.source_hash)
    return out


def _resume_hashes(candidate_id: str, gold_dir: Path) -> list[str]:
    """Unique résumé bronze-blob hashes for a candidate — silver primary, gold citations fallback.

    Returns ``[]`` when the candidate has no résumé (supply-sheet-only). Silver is the reliable
    link; gold ``PROFILE_PDF`` citations are a fallback **when** they carry a ``source_hash``.
    Silver lives at ``gold_dir.parent / "silver"`` (sibling of gold under the data root).
    """
    out: list[str] = []
    seen: set[str] = set()
    for h in _silver_resume_hashes(candidate_id, gold_dir.parent / "silver"):
        if h not in seen:
            seen.add(h)
            out.append(h)
    gold = read_gold(candidate_id, gold_dir)
    if gold is not None:
        for citations in [s.citations for s in gold.skills] + [d.citations for d in gold.domains]:
            for c in citations:
                if (
                    c.source is EvidenceSource.PROFILE_PDF
                    and c.source_hash
                    and c.source_hash not in seen
                ):
                    seen.add(c.source_hash)
                    out.append(c.source_hash)
    return out


def _candidate_view(pseudo_cand: Any, real_cand: Any, gold_dir: Path) -> CandidateView:
    """Pair the pseudonymous id (pre-render) with the de-anonymised identity (post-render)."""
    candidate_id = pseudo_cand.email  # AD-091: pre-render, email carries the candidate_id
    return CandidateView(
        candidate_id=candidate_id,
        name=real_cand.name,
        email=real_cand.email,
        source=real_cand.source.value,
        location=_location_str(real_cand.location),
        availability=_availability_str(real_cand.availability),
        years_experience=real_cand.years_experience,
        has_resume=bool(_resume_hashes(candidate_id, gold_dir)),
    )


def _build_view(
    pseudo: ShortlistResult | NoMatchResult,
    rendered: ShortlistResult | NoMatchResult,
    *,
    role_id: str,
    gold_dir: Path,
) -> MatchResponse:
    """Assemble the ``MatchResponse`` by zipping the parallel pseudo/rendered results.

    ``render_identities`` rewrites identity fields per-item in order, so the two results are
    structurally parallel — the pre-render result gives the stable ``candidate_id``, the rendered
    one the real name/email. ``run_id`` = ``role_id`` (single role per request, AD-050).
    """
    exclusions = [
        ExclusionView(
            candidate_id=pe.candidate_email,  # pre-render: the pseudonym (for /resume)
            display=re.candidate_email,  # post-render: real email (or candidate_id on vault miss)
            reason=pe.reason.value,
            detail=pe.detail,
        )
        for pe, re in zip(
            pseudo.exclusion_log.exclusions, rendered.exclusion_log.exclusions, strict=True
        )
    ]
    if isinstance(rendered, ShortlistResult) and isinstance(pseudo, ShortlistResult):
        shortlist = [
            AssessmentView(
                candidate=_candidate_view(pa.candidate, ra.candidate, gold_dir),
                skill_match_score=ra.skill_match_score,
                feedback_score=ra.feedback_score,
                combined_score=ra.combined_score,
                hard_skill_coverage=ra.hard_skill_coverage,
                desired_skill_coverage=ra.desired_skill_coverage,
                flags=[FlagView(type=f.type.value, message=f.message) for f in ra.flags],
                evidence=[EvidenceView(source=c.source.value, text=c.text) for c in ra.evidence],
                narrative=ra.narrative,
            )
            for pa, ra in zip(pseudo.ranked_assessments, rendered.ranked_assessments, strict=True)
        ]
        return MatchResponse(
            role_id=role_id,
            run_id=role_id,
            outcome="shortlist",
            shortlist=shortlist,
            exclusions=exclusions,
            total_eligible=rendered.total_eligible,
            config_snapshot=rendered.config_snapshot,
        )

    assert isinstance(rendered, NoMatchResult) and isinstance(pseudo, NoMatchResult)
    near = [
        NearMissView(
            candidate_id=pn.candidate_email,
            name=rn.name,
            reason=rn.reason,
            gap_summary=rn.gap_summary,
            selection_rationale=rn.selection_rationale,
        )
        for pn, rn in zip(pseudo.near_misses, rendered.near_misses, strict=True)
    ]
    closest = [
        NearMissView(
            candidate_id=pn.candidate_email,
            name=rn.name,
            reason=rn.reason,
            gap_summary=rn.gap_summary,
            selection_rationale=rn.selection_rationale,
        )
        for pn, rn in zip(pseudo.closest_on_skills, rendered.closest_on_skills, strict=True)
    ]
    return MatchResponse(
        role_id=role_id,
        run_id=role_id,
        outcome="no_match",
        no_match_reason=rendered.reason,
        near_misses=near,
        closest_on_skills=closest,
        exclusions=exclusions,
    )


# ---------------------------------------------------------------------------
# Résumé PDF (authorised-human surface — never to an LLM, AD-107)
# ---------------------------------------------------------------------------


def resume_pdf(
    candidate_id: str, *, gold_dir: Path | None = None, bronze_dir: Path | None = None
) -> bytes:
    """``GET /resume/{candidate_id}`` — the original résumé bytes from the bronze blob store.

    Resolves the blob via gold ``PROFILE_PDF`` citations. A candidate with no résumé citation
    (supply-sheet-only) raises ``ResumeNotFound`` (a normal 404, not an error). The bytes are for
    the authorised human browser only and are **never** passed to an LLM/embed endpoint.
    """
    hashes = _resume_hashes(candidate_id, gold_dir or _GOLD_DEFAULT)
    if not hashes:
        raise ResumeNotFound("No résumé on file for this candidate.")
    blobs = LocalFSBlobStore(bronze_dir or _BRONZE_DEFAULT)
    try:
        return blobs.get(hashes[0])
    except OSError:
        # gold cites a résumé blob but it is absent from bronze (pruned/unsynced). Surface a clean,
        # PII-safe 404 rather than letting a raw FileNotFoundError become an untyped 500 (FR-8).
        raise ResumeNotFound("Résumé file is not available in the store.") from None


# ---------------------------------------------------------------------------
# Decision capture (append-only; never feeds ranking — FR-7)
# ---------------------------------------------------------------------------


def record_decisions(
    req: DecisionRequest, *, decisions_dir: Path | None = None
) -> DecisionResponse:
    """``POST /decisions`` — append the human's put-forward / set-aside calls to a gitignored log.

    Keyed by the pseudonymous ``candidate_id`` (never a name). Capture-only: it records the call
    for audit and **never** alters this or any future shortlist (the learning loop is deferred).
    """
    out_dir = decisions_dir or _DECISIONS_DEFAULT
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_run = _SAFE_NAME.sub("_", req.run_id) or "run"
    recorded_at = datetime.now(UTC).isoformat()
    lines = [
        json.dumps(
            {
                "run_id": req.run_id,
                "role_id": req.role_id,
                "reviewer": req.reviewer,
                "candidate_id": d.candidate_id,
                "action": d.action,
                "reason": d.reason,
                "recorded_at": recorded_at,
            }
        )
        for d in req.decisions
    ]
    if lines:
        with (out_dir / f"{safe_run}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    return DecisionResponse(recorded=len(lines), run_id=req.run_id)
