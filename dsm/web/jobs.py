"""One-button ingest→index background job (c-011; AD-XXY; FR-4).

Runs the **same CLI commands** (`dsm ingest`, then `dsm index`) as subprocesses — the CLI body
owns the typer echo/exit semantics and the AD-108 OMP workaround, and a subprocess isolates the
docling/faiss memory from the serving process. Single-flight: one job at a time (409 while
running). The captured output is parsed into a structured summary from the pipeline's own
PII-safe summary lines; the raw log goes to a gitignored file under ``data/ingest_jobs/`` and
only its tail is surfaced on failure (the pipeline's stdout discipline keeps it PII-safe).

Milvus Lite is single-writer: if a match request holds the store at the moment the index step
runs, that step fails and the job reports ``failed`` — re-run (NF-4; single ``dsm serve``).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

import structlog

from dsm.web.models import IngestStatusResponse, IngestSummaryView
from dsm.web.service import WebServiceError

_log = structlog.get_logger(__name__)

_LOG_TAIL_LINES = 40


class JobAlreadyRunning(WebServiceError):
    status_code = 409


class JobPreconditionFailed(WebServiceError):
    status_code = 409


class _Job:
    def __init__(self, job_id: str, log_path: Path) -> None:
        self.job_id = job_id
        self.log_path = log_path
        self.state: str = "running"
        self.started_at = datetime.now(UTC)
        self.finished_at: datetime | None = None
        self.summary: IngestSummaryView | None = None


_lock = threading.Lock()
_current: _Job | None = None


_PATTERNS: dict[str, re.Pattern[str]] = {
    "landed": re.compile(r"^\s*landed\s*:\s*(\d+)", re.MULTILINE),
    "skipped": re.compile(r"^\s*skipped\s*:\s*(\d+)", re.MULTILINE),
    "gold_updated": re.compile(r"gold writes\s*:\s*updated=(\d+)"),
    "gold_unchanged": re.compile(r"gold writes\s*:\s*updated=\d+ unchanged=(\d+)"),
    "enrich_llm_calls": re.compile(r"enrich\s*:\s*llm_calls=(\d+)"),
    "enrich_cache_hits": re.compile(r"enrich\s*:\s*llm_calls=\d+ cache_hits=(\d+)"),
    "tombstoned": re.compile(r"^\s*tombstones\s*:\s*(\d+)", re.MULTILINE),
    "revived": re.compile(r"^\s*revived\s*:\s*(\d+)", re.MULTILINE),
    "indexed": re.compile(r"^\s*indexed\s*:\s*(\d+)", re.MULTILINE),
    "index_skipped_unchanged": re.compile(r"^\s*skipped-unchanged\s*:\s*(\d+)", re.MULTILINE),
    "index_removed": re.compile(r"^\s*tombstoned-removed\s*:\s*(\d+)", re.MULTILINE),
}


def parse_summary(output: str) -> IngestSummaryView:
    """Extract the structured counts from the two commands' PII-safe summary lines."""
    values = {
        name: int(m.group(1)) if (m := pattern.search(output)) else 0
        for name, pattern in _PATTERNS.items()
    }
    return IngestSummaryView(**values)


def _dsm_cmd() -> list[str]:
    """The ``dsm`` console script from this environment (same venv as the server)."""
    exe = Path(sys.executable).with_name("dsm")
    if exe.is_file():
        return [str(exe)]
    return [sys.executable, "-m", "dsm.cli.main"]  # fallback: module execution


def _run_pipeline(job: _Job, run: _Runner) -> None:
    output_parts: list[str] = []
    try:
        for args in (["ingest"], ["index"]):
            proc = run(_dsm_cmd() + args)
            output_parts.append(f"$ dsm {args[0]}\n{proc.stdout or ''}{proc.stderr or ''}")
            if proc.returncode != 0:
                job.state = "failed"
                break
        else:
            job.state = "succeeded"
    except Exception as exc:  # noqa: BLE001 — a crashed runner must not wedge single-flight
        output_parts.append(f"job runner error: {type(exc).__name__}")
        job.state = "failed"
    output = "\n".join(output_parts)
    job.summary = parse_summary(output)
    job.finished_at = datetime.now(UTC)
    try:
        job.log_path.parent.mkdir(parents=True, exist_ok=True)
        job.log_path.write_text(output, encoding="utf-8")
    except OSError:
        _log.warning("ingest_job.log_write_failed", job_id=job.job_id)
    _log.info("ingest_job.finished", job_id=job.job_id, state=job.state)


class _Runner:
    """Callable seam so tests can stub the subprocess (FR-4 tests never spawn a pipeline)."""

    def __call__(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=1800, check=False)


def start_ingest_job(*, jobs_dir: Path, runner: _Runner | None = None) -> IngestStatusResponse:
    """``POST /ingest/run`` — start the background ingest→index job (202) or 409 if running."""
    global _current
    if not os.environ.get("DSM_CANDIDATE_ID_KEY"):
        raise JobPreconditionFailed(
            "DSM_CANDIDATE_ID_KEY is not set in the server environment — ingest cannot run."
        )
    with _lock:
        if _current is not None and _current.state == "running":
            raise JobAlreadyRunning("An ingest job is already running — wait for it to finish.")
        job_id = f"job-{uuid.uuid4().hex[:8]}"
        job = _Job(job_id, jobs_dir / f"{job_id}.log")
        _current = job
    thread = threading.Thread(
        target=_run_pipeline, args=(job, runner or _Runner()), name=job_id, daemon=True
    )
    thread.start()
    return job_status()


def job_status() -> IngestStatusResponse:
    """``GET /ingest/status`` — the current/last job's state, summary, and failure log tail."""
    job = _current
    if job is None:
        return IngestStatusResponse(state="idle", job_id=None)
    log_tail: list[str] = []
    if job.state == "failed" and job.log_path.is_file():
        try:
            log_tail = job.log_path.read_text(encoding="utf-8").splitlines()[-_LOG_TAIL_LINES:]
        except OSError:
            log_tail = []
    return IngestStatusResponse(
        state=job.state,  # type: ignore[arg-type]
        job_id=job.job_id,
        started_at=job.started_at.isoformat(),
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
        summary=job.summary,
        log_tail=log_tail,
    )


def reset_jobs_for_tests() -> None:
    """Test hook: clear the single-flight slot between cases."""
    global _current
    with _lock:
        _current = None
