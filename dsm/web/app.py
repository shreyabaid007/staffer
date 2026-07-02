"""FastAPI app (c-008; AD-XXX) — routes + PII-safe error handling over the web service.

Thin transport edge: every route delegates to ``dsm.web.service`` (which reuses the CLI spine). The
data roots are injected via the ``get_paths`` dependency so tests can point them at tmp dirs
(``app.dependency_overrides[get_paths] = ...``). No ``dsm.pii`` import here — the boundary lives
in the reused CLI builders; de-anonymisation is at the service's output edge (AD-107).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from dsm.web import jobs, service, supply
from dsm.web.models import (
    AddCandidateRequest,
    AttachmentResponse,
    Category,
    DecisionRequest,
    DecisionResponse,
    DemandParseResponse,
    FeedbackWriteRequest,
    IngestStatusResponse,
    IntakeRequest,
    IntakeResponse,
    MatchResponse,
    SupplyResponse,
    SupplyRowView,
)
from dsm.web.service import WebServiceError

_STATIC = Path(__file__).resolve().parent / "static"


@dataclass
class WebPaths:
    """The data roots the web layer reads — overridable in tests via ``get_paths``."""

    gold_dir: Path
    bronze_dir: Path
    decisions_dir: Path
    vault_path: Path | None = None
    db_path: str = ""
    raw_dir: Path | None = None  # c-011: supply CSVs + resumes + feedback (defaults below)
    jobs_dir: Path | None = None  # c-011: ingest-job logs

    @property
    def raw(self) -> Path:
        return self.raw_dir if self.raw_dir is not None else service._RAW_DEFAULT

    @property
    def jobs(self) -> Path:
        return self.jobs_dir if self.jobs_dir is not None else service._JOBS_DEFAULT


def get_paths() -> WebPaths:
    """Default data roots (the same ones the CLI uses). Overridden in tests."""
    return WebPaths(
        gold_dir=service._GOLD_DEFAULT,
        bronze_dir=service._BRONZE_DEFAULT,
        decisions_dir=service._DECISIONS_DEFAULT,
    )


app = FastAPI(title="Staffer", docs_url="/api", redoc_url=None)


@app.exception_handler(WebServiceError)
async def _service_error_handler(_request: Request, exc: WebServiceError) -> JSONResponse:
    """Map every typed service error to its status with a PII-safe ``{detail}`` body (FR-8)."""
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness check — no LLM / Milvus / gold access (FR-1-AC-2)."""
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Serve the single matcher page (FR-1-AC-1)."""
    return FileResponse(_STATIC / "index.html", media_type="text/html")


_Paths = Annotated[WebPaths, Depends(get_paths)]


@app.post("/intake", response_model=IntakeResponse)
def intake(req: IntakeRequest, paths: _Paths) -> IntakeResponse:
    """Parse prose → role echo, or the missing gate fields to clarify (no gate runs)."""
    return service.intake_echo(req.prose, gold_dir=paths.gold_dir)


@app.post("/match/query", response_model=MatchResponse)
def match_query(req: IntakeRequest, paths: _Paths) -> MatchResponse:
    """NL door: confirm + run → an explainable shortlist (or no-match)."""
    return service.match_query(
        req, gold_dir=paths.gold_dir, db_path=paths.db_path, vault_path=paths.vault_path
    )


@app.post("/demand/parse", response_model=DemandParseResponse)
def demand_parse(file: Annotated[UploadFile, File()]) -> DemandParseResponse:
    """Parse an uploaded Open Roles CSV into the role picker (no match yet)."""
    return service.demand_parse(file.file.read())


@app.post("/match/role", response_model=MatchResponse)
def match_role(
    role_id: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    paths: _Paths,
) -> MatchResponse:
    """CSV door: select ``role_id`` from the upload and run → shortlist (or no-match)."""
    return service.match_role(
        file.file.read(),
        role_id,
        gold_dir=paths.gold_dir,
        db_path=paths.db_path,
        vault_path=paths.vault_path,
    )


@app.get("/resume/{candidate_id}")
def resume(candidate_id: str, paths: _Paths) -> Response:
    """Stream the candidate's original résumé PDF (authorised-human surface; never to an LLM)."""
    data = service.resume_pdf(candidate_id, gold_dir=paths.gold_dir, bronze_dir=paths.bronze_dir)
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="resume.pdf"'},
    )


@app.post("/decisions", response_model=DecisionResponse)
def decisions(req: DecisionRequest, paths: _Paths) -> DecisionResponse:
    """Record put-forward / set-aside decisions (append-only; never feeds ranking — FR-7)."""
    return service.record_decisions(req, decisions_dir=paths.decisions_dir)


# ---------------------------------------------------------------------------
# Supply management + ingest trigger (c-011; AD-XXY)
# ---------------------------------------------------------------------------


@app.get("/supply", response_model=SupplyResponse)
def supply_read(paths: _Paths) -> SupplyResponse:
    """All three category sheets from the current raw CSVs + per-row sync status (FR-1)."""
    return supply.read_supply(raw_dir=paths.raw, gold_dir=paths.gold_dir)


@app.post("/supply/candidates", response_model=SupplyRowView, status_code=201)
def supply_add(req: AddCandidateRequest, paths: _Paths) -> SupplyRowView:
    """Append a candidate row to its category sheet (banner bumped; 409 duplicate — FR-2)."""
    return supply.add_candidate(req, raw_dir=paths.raw, gold_dir=paths.gold_dir)


# NOTE: the attachment routes (`…/{email}/resume`, `…/{email}/feedback…`) are registered
# BEFORE the two-param row delete (`…/{category}/{email}`) — Starlette matches in order, and
# the literal-tail patterns must win over the generic two-segment one.


@app.post("/supply/candidates/{email}/resume", response_model=AttachmentResponse)
def resume_upload(
    email: str, paths: _Paths, file: Annotated[UploadFile, File()]
) -> AttachmentResponse:
    """Store the resume PDF (replace-on-re-upload) + pre-ingest link check (FR-3-AC-1/2)."""
    return supply.store_resume(email, file.file.read(), raw_dir=paths.raw)


@app.delete("/supply/candidates/{email}/resume", status_code=204)
def resume_delete(email: str, paths: _Paths) -> None:
    """Delete the web-uploaded resume PDF (the next ingest reflects the removal — FR-3-AC-4)."""
    supply.delete_resume(email, raw_dir=paths.raw)


@app.post("/supply/candidates/{email}/feedback", response_model=AttachmentResponse)
def feedback_write(email: str, req: FeedbackWriteRequest, paths: _Paths) -> AttachmentResponse:
    """Store written-Markdown feedback, guaranteed to link to this candidate (FR-3-AC-3)."""
    return supply.store_feedback(email, req.text, source=req.source, raw_dir=paths.raw)


@app.post("/supply/candidates/{email}/feedback/file", response_model=AttachmentResponse)
def feedback_upload(
    email: str, paths: _Paths, file: Annotated[UploadFile, File()]
) -> AttachmentResponse:
    """Store an uploaded feedback file (.md/.txt) with the same guaranteed-link pass."""
    name = (file.filename or "").lower()
    if not name.endswith((".md", ".txt")):
        raise supply.InvalidUpload("Feedback upload must be a .md or .txt file.")
    text = file.file.read().decode("utf-8", errors="replace")
    return supply.store_feedback(email, text, source="internal_ee", raw_dir=paths.raw)


@app.delete("/supply/candidates/{email}/feedback/{filename}", status_code=204)
def feedback_delete(email: str, filename: str, paths: _Paths) -> None:
    """Delete one linked feedback file (traversal-guarded — FR-3-AC-4)."""
    supply.delete_feedback(email, filename, raw_dir=paths.raw)


@app.delete("/supply/candidates/{category}/{email}", status_code=204)
def supply_remove(category: Category, email: str, paths: _Paths) -> None:
    """Remove the row by email from the category sheet (the next ingest tombstones — FR-2)."""
    supply.remove_candidate(category, email, raw_dir=paths.raw)


@app.post("/ingest/run", response_model=IngestStatusResponse, status_code=202)
def ingest_run(paths: _Paths) -> IngestStatusResponse:
    """Start the background ingest→index job (single-flight: 409 while running — FR-4)."""
    return jobs.start_ingest_job(jobs_dir=paths.jobs)


@app.get("/ingest/status", response_model=IngestStatusResponse)
def ingest_status() -> IngestStatusResponse:
    """The current/last job's state + the parsed incremental summary (FR-4-AC-1)."""
    return jobs.job_status()
