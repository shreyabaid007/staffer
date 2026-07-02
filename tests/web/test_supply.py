"""Supply management API tests (c-011 T-003/T-004/T-005/T-006; FR-1..FR-4).

TestClient over tmp data roots (``get_paths`` override) — no LLM, no Milvus, no subprocess
(the job runner is stubbed). The feedback round-trips go through the REAL ``parse_markdown``
so "guaranteed to link" is verified by the parser that ingest itself uses.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import dsm.cli.commands as commands
import dsm.web.jobs as jobs
from dsm.ingest.parse.markdown import parse_markdown
from dsm.web.app import WebPaths, app, get_paths

_BEACH = (
    "Beach - Parity - as of 2026-06-01 (synthetic),,,,,,,,\n"
    "#,Name,Email,Grade,Key Skills,Location,Chennai-open,Days on Beach,Notes\n"
    '1,Karan Mehta,karan@pp.example,Lead Consultant,"Java, Kotlin",Bengaluru,Yes,37,Beach note\n'
)
_ROLLING = (
    "Rolling Off - Parity - as of 2026-06-01 (synthetic),,,,,,,,,,\n"
    "#,Name,Email,Grade,Key Skills,Current Client,Roll-off Date,Confidence,Location,"
    "Chennai-open,Notes\n"
    '1,Meera Nair,meera@pp.example,Lead Consultant,"Kotlin, Kafka",Acme,2026-08-18,low,'
    "Bengaluru,No,Uncertain\n"
)


@pytest.fixture()
def data_root(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    (raw / "supply").mkdir(parents=True)
    (raw / "resumes").mkdir()
    (raw / "feedback").mkdir()
    (raw / "supply" / "Beach.csv").write_text(_BEACH, encoding="utf-8")
    (raw / "supply" / "Rolling Off.csv").write_text(_ROLLING, encoding="utf-8")
    (tmp_path / "gold").mkdir()
    return tmp_path


@pytest.fixture()
def client(data_root: Path, monkeypatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DSM_CANDIDATE_ID_KEY", "test-key")
    app.dependency_overrides[get_paths] = lambda: WebPaths(
        gold_dir=data_root / "gold",
        bronze_dir=data_root / "bronze",
        decisions_dir=data_root / "decisions",
        raw_dir=data_root / "raw",
        jobs_dir=data_root / "jobs",
    )
    jobs.reset_jobs_for_tests()
    yield TestClient(app)
    app.dependency_overrides.clear()
    jobs.reset_jobs_for_tests()


# ---------------------------------------------------------------------------
# GET /supply (FR-1)
# ---------------------------------------------------------------------------


class TestSupplyRead:
    def test_returns_all_three_sheets_with_rows(self, client: TestClient) -> None:
        body = client.get("/supply").json()
        by_cat = {s["category"]: s for s in body["sheets"]}
        assert set(by_cat) == {"beach", "rolling_off", "new_joiner"}
        assert by_cat["beach"]["as_of"] == "2026-06-01"
        [karan] = by_cat["beach"]["rows"]
        assert karan["name"] == "Karan Mehta"
        assert karan["skills"] == ["Java", "Kotlin"]
        assert karan["chennai_open"] is True
        assert karan["days_on_beach"] == 37
        assert karan["candidate_id"] == commands.derive_candidate_id("karan@pp.example")
        [meera] = by_cat["rolling_off"]["rows"]
        assert meera["roll_off_date"] == "2026-08-18"
        assert meera["confidence"] == "low"
        assert by_cat["new_joiner"]["rows"] == []  # sheet absent → empty, never an error

    def test_not_ingested_until_gold_exists(self, client: TestClient) -> None:
        row = client.get("/supply").json()["sheets"][0]["rows"][0]
        assert row["ingested"] is False
        assert row["feedback_count"] == 0


# ---------------------------------------------------------------------------
# POST/DELETE /supply/candidates (FR-2)
# ---------------------------------------------------------------------------


_NEW = {
    "category": "beach",
    "name": "Neha Gupta",
    "email": "neha@pp.example",
    "grade": "Senior Consultant",
    "skills": ["react", "typescript"],
    "location": "Mumbai",
    "chennai_open": True,
}


class TestSupplyMutate:
    def test_add_appends_row_and_bumps_banner(self, client: TestClient, data_root: Path) -> None:
        resp = client.post("/supply/candidates", json=_NEW)
        assert resp.status_code == 201, resp.text
        assert resp.json()["ingested"] is False  # pending until the pipeline runs

        text = (data_root / "raw" / "supply" / "Beach.csv").read_text()
        assert "neha@pp.example" in text
        assert "as of 2026-06-01" not in text  # banner bumped to today
        # The re-read survives its own writer (round-trip through GET).
        rows = client.get("/supply").json()["sheets"][0]["rows"]
        assert [r["name"] for r in rows] == ["Karan Mehta", "Neha Gupta"]
        assert rows[1]["skills"] == ["react", "typescript"]

    def test_duplicate_email_is_409(self, client: TestClient) -> None:
        assert client.post("/supply/candidates", json=_NEW).status_code == 201
        resp = client.post("/supply/candidates", json=_NEW)
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]

    def test_category_required_fields_422(self, client: TestClient) -> None:
        bad = {**_NEW, "category": "rolling_off"}  # no roll_off_date/confidence
        assert client.post("/supply/candidates", json=bad).status_code == 422
        bad = {**_NEW, "category": "new_joiner"}  # no join_date
        assert client.post("/supply/candidates", json=bad).status_code == 422
        ok = {**_NEW, "category": "new_joiner", "join_date": "2026-08-01"}
        assert client.post("/supply/candidates", json=ok).status_code == 201

    def test_delete_removes_row(self, client: TestClient, data_root: Path) -> None:
        resp = client.delete("/supply/candidates/beach/karan@pp.example")
        assert resp.status_code == 204
        assert "karan@pp.example" not in (data_root / "raw" / "supply" / "Beach.csv").read_text()
        assert client.get("/supply").json()["sheets"][0]["rows"] == []

    def test_delete_unknown_email_is_404(self, client: TestClient) -> None:
        assert client.delete("/supply/candidates/beach/ghost@pp.example").status_code == 404

    def test_add_creates_missing_sheet(self, client: TestClient, data_root: Path) -> None:
        req = {**_NEW, "category": "new_joiner", "join_date": "2026-08-01"}
        assert client.post("/supply/candidates", json=req).status_code == 201
        created = data_root / "raw" / "supply" / "New Joiners.csv"
        assert created.is_file()
        assert "Join Date" in created.read_text()


# ---------------------------------------------------------------------------
# Attachments (FR-3)
# ---------------------------------------------------------------------------


class TestResumeUpload:
    def test_pdf_stored_with_link_check(self, client: TestClient, data_root: Path) -> None:
        pdf = b"%PDF-1.4 fake body containing karan@pp.example for the link check"
        resp = client.post(
            "/supply/candidates/karan@pp.example/resume",
            files={"file": ("karan.pdf", pdf, "application/pdf")},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body == {"stored": "karan.pdf", "link_check": "ok"}
        assert (data_root / "raw" / "resumes" / "karan.pdf").read_bytes() == pdf
        # Surfaces on the supply row immediately (pending-ingest resume).
        row = client.get("/supply").json()["sheets"][0]["rows"][0]
        assert row["has_resume"] is True

    def test_non_pdf_rejected(self, client: TestClient) -> None:
        resp = client.post(
            "/supply/candidates/karan@pp.example/resume",
            files={"file": ("cv.docx", b"PK\x03\x04word", "application/octet-stream")},
        )
        assert resp.status_code == 422

    def test_delete_resume(self, client: TestClient, data_root: Path) -> None:
        pdf = b"%PDF-1.4 karan@pp.example"
        client.post(
            "/supply/candidates/karan@pp.example/resume",
            files={"file": ("karan.pdf", pdf, "application/pdf")},
        )
        assert client.delete("/supply/candidates/karan@pp.example/resume").status_code == 204
        assert not (data_root / "raw" / "resumes" / "karan.pdf").exists()
        assert client.delete("/supply/candidates/karan@pp.example/resume").status_code == 404


class TestFeedback:
    def test_written_markdown_links_via_real_parser(
        self, client: TestClient, data_root: Path
    ) -> None:
        resp = client.post(
            "/supply/candidates/karan@pp.example/feedback",
            json={"text": "Strong on payments delivery.\n- great comms", "source": "client"},
        )
        assert resp.status_code == 200, resp.text
        stored = resp.json()["stored"]
        path = data_root / "raw" / "feedback" / stored
        records = parse_markdown(path.read_bytes(), "sha256:t", run_id="t")
        assert records, "web-written feedback must parse into records"
        assert records[0].raw["email_key"] == "karan@pp.example"
        assert records[0].raw["kind"] == "client"  # source=client → client heading

    def test_existing_email_key_is_preserved(self, client: TestClient, data_root: Path) -> None:
        text = "email: karan@pp.example\n\n## Client feedback - Sterling\nDependable engineer."
        resp = client.post(
            "/supply/candidates/karan@pp.example/feedback",
            json={"text": text, "source": "internal_ee"},
        )
        assert resp.status_code == 200
        path = data_root / "raw" / "feedback" / resp.json()["stored"]
        records = parse_markdown(path.read_bytes(), "sha256:t", run_id="t")
        assert records[0].raw["email_key"] == "karan@pp.example"

    def test_conflicting_explicit_key_rejected(self, client: TestClient) -> None:
        # An explicit `email:` key line for ANOTHER candidate would link the file to them —
        # rejected. (A mere mention of another email in prose is fine: our key line wins.)
        resp = client.post(
            "/supply/candidates/karan@pp.example/feedback",
            json={"text": "email: meera@pp.example\n\nGreat work on the platform."},
        )
        assert resp.status_code == 422
        assert "conflicting email" in resp.json()["detail"]

    def test_prose_mention_of_another_email_is_fine(self, client: TestClient) -> None:
        resp = client.post(
            "/supply/candidates/karan@pp.example/feedback",
            json={"text": "Paired well with meera@pp.example on the platform."},
        )
        assert resp.status_code == 200
        assert resp.json()["link_check"] == "ok"

    def test_file_upload_and_delete(self, client: TestClient, data_root: Path) -> None:
        resp = client.post(
            "/supply/candidates/karan@pp.example/feedback/file",
            files={"file": ("review.md", b"## Project feedback\nSolid work.", "text/markdown")},
        )
        assert resp.status_code == 200
        stored = resp.json()["stored"]
        assert (
            client.delete(f"/supply/candidates/karan@pp.example/feedback/{stored}").status_code
            == 204
        )
        assert not (data_root / "raw" / "feedback" / stored).exists()

    def test_delete_traversal_guarded(self, client: TestClient) -> None:
        for name in ("..%2F..%2Fetc", "..", "a%2Fb.md"):
            resp = client.delete(f"/supply/candidates/karan@pp.example/feedback/{name}")
            assert resp.status_code in (404, 422), name

    def test_cannot_delete_another_candidates_feedback(
        self, client: TestClient, data_root: Path
    ) -> None:
        stored = client.post(
            "/supply/candidates/karan@pp.example/feedback",
            json={"text": "good"},
        ).json()["stored"]
        resp = client.delete(f"/supply/candidates/meera@pp.example/feedback/{stored}")
        assert resp.status_code == 404
        assert (data_root / "raw" / "feedback" / stored).exists()


# ---------------------------------------------------------------------------
# Ingest job (FR-4) — subprocess stubbed
# ---------------------------------------------------------------------------

_INGEST_OUT = """
── Land ── run_id=run-x
  landed : 1
  skipped: 5
── Gold ──
  entities    : 12
  gold writes : updated=2 unchanged=10
  enrich      : llm_calls=1 cache_hits=9
  tombstones  : 1
  revived     : 0
"""
_INDEX_OUT = """
── Index ── run_id=run-y
  indexed           : 2
  skipped-unchanged : 10
  tombstoned-removed: 1
  thin-skipped      : 0
"""


class _FakeRunner:
    def __init__(self, returncode: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode

    def __call__(self, cmd):  # noqa: ANN001, ANN204 — mirrors subprocess.run's surface
        import subprocess

        self.calls.append(cmd)
        out = _INGEST_OUT if cmd[-1] == "ingest" else _INDEX_OUT
        return subprocess.CompletedProcess(cmd, self.returncode, stdout=out, stderr="")


class TestIngestJob:
    def test_run_returns_202_and_parses_summary(self, client: TestClient, monkeypatch) -> None:
        fake = _FakeRunner()
        monkeypatch.setattr(jobs, "_Runner", lambda: fake)
        resp = client.post("/ingest/run")
        assert resp.status_code == 202, resp.text
        # The stubbed pipeline finishes fast; poll the status until done.
        for _ in range(100):
            status = client.get("/ingest/status").json()
            if status["state"] != "running":
                break
        assert status["state"] == "succeeded"
        assert [c[-1] for c in fake.calls] == ["ingest", "index"]
        summary = status["summary"]
        assert summary["landed"] == 1
        assert summary["gold_updated"] == 2
        assert summary["gold_unchanged"] == 10
        assert summary["enrich_llm_calls"] == 1
        assert summary["enrich_cache_hits"] == 9
        assert summary["tombstoned"] == 1
        assert summary["indexed"] == 2
        assert summary["index_skipped_unchanged"] == 10
        assert summary["index_removed"] == 1

    def test_single_flight_409(self, client: TestClient, monkeypatch) -> None:
        import threading

        release = threading.Event()

        class _Blocking:
            def __call__(self, cmd):  # noqa: ANN001, ANN204
                import subprocess

                release.wait(timeout=5)
                return subprocess.CompletedProcess(cmd, 0, stdout=_INGEST_OUT, stderr="")

        monkeypatch.setattr(jobs, "_Runner", lambda: _Blocking())
        assert client.post("/ingest/run").status_code == 202
        assert client.post("/ingest/run").status_code == 409
        release.set()

    def test_failed_step_reports_log_tail(self, client: TestClient, monkeypatch) -> None:
        fake = _FakeRunner(returncode=1)
        monkeypatch.setattr(jobs, "_Runner", lambda: fake)
        client.post("/ingest/run")
        for _ in range(100):
            status = client.get("/ingest/status").json()
            if status["state"] != "running":
                break
        assert status["state"] == "failed"
        assert [c[-1] for c in fake.calls] == ["ingest"]  # stopped at the failing step
        assert status["log_tail"], "a failed job must surface its log tail"

    def test_missing_key_is_409(self, client: TestClient, monkeypatch) -> None:
        monkeypatch.delenv("DSM_CANDIDATE_ID_KEY", raising=False)
        resp = client.post("/ingest/run")
        assert resp.status_code == 409
        assert "DSM_CANDIDATE_ID_KEY" in resp.json()["detail"]

    def test_idle_before_any_run(self, client: TestClient) -> None:
        assert client.get("/ingest/status").json()["state"] == "idle"
