"""Web API tests (c-008) — FastAPI TestClient over the spine with monkeypatched builders.

Mirrors ``tests/cli/test_orchestrator.py``: tmp gold via ``write_gold``, vault seeded via
``FileVault``, the live ``commands._build_*`` builders replaced with deterministic stubs (no
LLM / Modal / Milvus). The data roots are injected via ``get_paths`` overrides. No network.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import dsm.cli.commands as commands
from dsm.ingest.blobstore import LocalFSBlobStore, hash_bytes
from dsm.ingest.goldstore import write_gold
from dsm.ingest.models import (
    Confidence,
    GoldCandidate,
    Grade,
    MergedSkill,
    NormalizedRecord,
    Sourced,
    SourceType,
)
from dsm.match.intake import NullIntakeCache
from dsm.match.models import RoleIntake, ScoreExtraction
from dsm.models import (
    Candidate,
    EvidenceCitation,
    EvidenceSource,
    FreeNow,
    Location,
    ProficiencyLevel,
    SkillDepth,
    SkillRequirement,
    TargetProfileScorecard,
)
from dsm.web.app import WebPaths, app, get_paths

_CSV = (
    "Open Roles - Acme - as of 2026-06-15\n"
    "Role ID,Title,Required Skills,Start,Location,Co-location,Priority,Notes / Constraints\n"
    "ROLE-Q1,Backend Engineer,kotlin (advanced),2026-07-01,Chennai,Yes,1,\n"
)


def _predict(scorecard: TargetProfileScorecard, candidate: Candidate) -> ScoreExtraction:
    return ScoreExtraction(
        skill_match_score=0.8, feedback_score=0.5, narrative="strong kotlin fit"
    )


def _gold(
    cid: str,
    *,
    city: str = "Chennai",
    skill: str = "kotlin",
    valid_as_of: date,
    resume_hash: str | None = None,
) -> GoldCandidate:
    citations = (
        [
            EvidenceCitation(
                source=EvidenceSource.PROFILE_PDF, text="Kotlin work", source_hash=resume_hash
            )
        ]
        if resume_hash
        else []
    )
    return GoldCandidate(
        candidate_id=cid,
        name_vault_ref=f"name:{cid}",
        email_vault_ref=f"email:{cid}",
        grade=Sourced(value=Grade.LEAD_CONSULTANT),
        location=Sourced(value=Location(city=city)),
        availability=Sourced(value=FreeNow()),
        skills=[
            MergedSkill(
                name=skill,
                proficiency=ProficiencyLevel.ADVANCED,
                confidence=Confidence.MEDIUM,
                citations=citations,
            )
        ],
        valid_as_of=valid_as_of,
        gold_hash=f"sha256:{cid}",
        merge_version="merge-v1",
        prompt_version="enrich-v1",
        model_version="anthropic/claude-sonnet-4-6",
    )


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the live builders so the spine runs with no LLM / Modal / Milvus."""
    monkeypatch.setattr(commands, "_build_clarify_predictor", lambda config: None)  # → echo path
    monkeypatch.setattr(commands, "_build_score_predictor", lambda config: _predict)
    monkeypatch.setattr(commands, "_build_embed_client", lambda: None)  # skip recall/rerank
    monkeypatch.setattr(commands, "_build_query_store", lambda config, db_path="": None)
    monkeypatch.setattr(commands, "_build_near_miss_rationale_predictor", lambda config: None)


def _patch_intake(monkeypatch: pytest.MonkeyPatch, predict) -> None:
    """Stub the NL intake predictor (and bypass the file cache) for the NL door."""
    monkeypatch.setattr(commands, "_build_intake_cache", lambda config: NullIntakeCache())
    monkeypatch.setattr(commands, "_build_intake_predictor", lambda config: predict)


Builder = Callable[..., TestClient]


@pytest.fixture
def make_client(tmp_path: Path) -> Iterator[Builder]:
    """Build a TestClient with the data roots pointed at tmp dirs (overrides ``get_paths``)."""

    def build(
        golds: list[GoldCandidate],
        *,
        vault: dict[str, tuple[str, str]] | None = None,
        resume_blobs: list[bytes] | None = None,
        silver_resumes: list[tuple[str, str]] | None = None,
    ) -> TestClient:
        gold_dir = tmp_path / "gold"
        bronze_dir = tmp_path / "bronze"
        decisions_dir = tmp_path / "decisions"
        vault_path = tmp_path / "identity" / "vault.json"
        for g in golds:
            write_gold(g, gold_dir)
        if silver_resumes:
            recs = tmp_path / "silver" / "records"  # sibling of gold, per the data layout
            recs.mkdir(parents=True, exist_ok=True)
            lines = [
                NormalizedRecord(
                    candidate_id=cid,
                    source_type=SourceType.RESUME,
                    source_hash=h,
                    extractor_version="test",
                ).model_dump_json()
                for cid, h in silver_resumes
            ]
            (recs / "seed.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        if vault:
            from dsm.pii.vault import FileVault

            fv = FileVault(vault_path)
            for cid, (name, email) in vault.items():
                fv.put_identity(cid, name, email)
        if resume_blobs:
            store = LocalFSBlobStore(bronze_dir)
            for data in resume_blobs:
                store.put(data)
        app.dependency_overrides[get_paths] = lambda: WebPaths(
            gold_dir=gold_dir,
            bronze_dir=bronze_dir,
            decisions_dir=decisions_dir,
            vault_path=vault_path,
            db_path="",
        )
        return TestClient(app)

    yield build
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Static surface
# ---------------------------------------------------------------------------


def test_healthz() -> None:
    client = TestClient(app)
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_index_is_html() -> None:
    res = TestClient(app).get("/")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert "Staffer" in res.text


# ---------------------------------------------------------------------------
# CSV door
# ---------------------------------------------------------------------------


def test_match_role_returns_shortlist(wired: None, make_client: Builder) -> None:
    client = make_client([_gold("cid:a", valid_as_of=date(2026, 6, 10))])
    res = client.post(
        "/match/role",
        data={"role_id": "ROLE-Q1"},
        files={"file": ("open_roles.csv", _CSV, "text/csv")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["outcome"] == "shortlist"
    assert [a["candidate"]["candidate_id"] for a in body["shortlist"]] == ["cid:a"]
    assert body["shortlist"][0]["combined_score"] == pytest.approx(0.8 * 0.7 + 0.5 * 0.3)
    assert body["run_id"] == "ROLE-Q1"


def test_match_role_deanonymises_from_vault(wired: None, make_client: Builder) -> None:
    client = make_client(
        [_gold("cid:a", valid_as_of=date(2026, 6, 10))],
        vault={"cid:a": ("Ada Lovelace", "ada@example.com")},
    )
    res = client.post(
        "/match/role",
        data={"role_id": "ROLE-Q1"},
        files={"file": ("open_roles.csv", _CSV, "text/csv")},
    )
    cand = res.json()["shortlist"][0]["candidate"]
    assert cand["name"] == "Ada Lovelace"
    assert cand["email"] == "ada@example.com"
    assert cand["candidate_id"] == "cid:a"  # pseudonym kept for /resume + /decisions addressing


def test_match_role_unknown_role_id_404(wired: None, make_client: Builder) -> None:
    client = make_client([_gold("cid:a", valid_as_of=date(2026, 6, 10))])
    res = client.post(
        "/match/role",
        data={"role_id": "ROLE-NOPE"},
        files={"file": ("open_roles.csv", _CSV, "text/csv")},
    )
    assert res.status_code == 404


def test_match_role_bad_csv_400(wired: None, make_client: Builder) -> None:
    client = make_client([_gold("cid:a", valid_as_of=date(2026, 6, 10))])
    res = client.post(
        "/match/role",
        data={"role_id": "ROLE-Q1"},
        files={"file": ("x.csv", "not,a,valid,banner\n", "text/csv")},
    )
    assert res.status_code == 400


def test_match_role_stale_supply_refuses_409(wired: None, make_client: Builder) -> None:
    client = make_client([_gold("cid:a", valid_as_of=date(2026, 4, 1))])
    res = client.post(
        "/match/role",
        data={"role_id": "ROLE-Q1"},
        files={"file": ("open_roles.csv", _CSV, "text/csv")},
    )
    assert res.status_code == 409
    assert isinstance(res.json()["detail"], str)


def test_demand_parse_lists_roles(wired: None, make_client: Builder) -> None:
    client = make_client([])
    res = client.post("/demand/parse", files={"file": ("open_roles.csv", _CSV, "text/csv")})
    assert res.status_code == 200
    body = res.json()
    assert body["demand_as_of"] == "2026-06-15"
    assert [r["role_id"] for r in body["roles"]] == ["ROLE-Q1"]


# ---------------------------------------------------------------------------
# NL door
# ---------------------------------------------------------------------------


def _intake(
    *,
    city: str | None = "Bengaluru",
    start: str | None = "2026-08-01",
    exclude: tuple[str, ...] = (),
):
    def predict(prose: str, today: date) -> RoleIntake:
        return RoleIntake(
            title="Senior Kotlin Engineer",
            hard_skills=[SkillRequirement(name="kotlin", depth=SkillDepth.HARD)],
            location_city=city,
            start_date_iso=start,
            start_date_phrase="next month" if start else None,
            exclude_cities=list(exclude),
        )

    return predict


def test_intake_ready_echo(
    wired: None, make_client: Builder, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_intake(monkeypatch, _intake())
    client = make_client([])
    res = client.post("/intake", json={"prose": "Senior Kotlin engineer in Bengaluru next month"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ready"
    assert body["echo"]["title"] == "Senior Kotlin Engineer"
    assert body["echo"]["location"] == "Bengaluru"
    assert body["echo"]["co_location_required"] is True  # Python-derived (AD-002)
    assert body["echo"]["hard_skills"] == ["kotlin"]


def test_intake_needs_clarification(
    wired: None, make_client: Builder, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_intake(monkeypatch, _intake(city=None, start=None))
    client = make_client([])
    res = client.post("/intake", json={"prose": "Need a kotlin engineer"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "needs_clarification"
    assert set(body["missing"]) == {"location", "start"}


def test_intake_empty_prose_400(make_client: Builder) -> None:
    client = make_client([])
    res = client.post("/intake", json={"prose": "   "})
    assert res.status_code == 400


def test_match_query_returns_shortlist(
    wired: None, make_client: Builder, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_intake(monkeypatch, _intake())
    client = make_client([_gold("cid:a", city="Bengaluru", valid_as_of=date(2026, 6, 20))])
    res = client.post(
        "/match/query",
        json={"prose": "Senior Kotlin engineer in Bengaluru next month", "confirm": True},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["outcome"] == "shortlist"
    assert [a["candidate"]["candidate_id"] for a in body["shortlist"]] == ["cid:a"]


def test_match_query_still_missing_422(
    wired: None, make_client: Builder, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_intake(monkeypatch, _intake(city=None, start=None))
    client = make_client([_gold("cid:a", city="Bengaluru", valid_as_of=date(2026, 6, 20))])
    res = client.post("/match/query", json={"prose": "kotlin engineer", "confirm": True})
    assert res.status_code == 422


def test_match_query_clarification_resolves(
    wired: None, make_client: Builder, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Missing location from the parse; the operator supplies it → assembles + matches.
    _patch_intake(monkeypatch, _intake(city=None, start="2026-08-01"))
    client = make_client([_gold("cid:a", city="Bengaluru", valid_as_of=date(2026, 6, 20))])
    res = client.post(
        "/match/query",
        json={
            "prose": "kotlin engineer next month",
            "confirm": True,
            "clarifications": {"location": "Bengaluru"},
        },
    )
    assert res.status_code == 200
    assert res.json()["outcome"] == "shortlist"


# ---------------------------------------------------------------------------
# Résumé PDF (authorised-human surface)
# ---------------------------------------------------------------------------


def test_resume_streams_pdf(wired: None, make_client: Builder) -> None:
    pdf = b"%PDF-1.4 fake resume bytes"
    h = hash_bytes(pdf)
    client = make_client(
        [_gold("cid:a", valid_as_of=date(2026, 6, 10), resume_hash=h)],
        resume_blobs=[pdf],
    )
    res = client.get("/resume/cid:a")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content == pdf


def test_resume_supply_only_404(wired: None, make_client: Builder) -> None:
    client = make_client(
        [_gold("cid:b", valid_as_of=date(2026, 6, 10))]
    )  # no PROFILE_PDF citation
    res = client.get("/resume/cid:b")
    assert res.status_code == 404


def test_resume_via_silver(wired: None, make_client: Builder) -> None:
    """Résumé resolves via the silver RESUME record even when gold citations lack a source_hash."""
    pdf = b"%PDF-1.4 via silver"
    h = hash_bytes(pdf)
    client = make_client(
        [_gold("cid:a", valid_as_of=date(2026, 6, 10))],  # gold carries no profile_pdf source_hash
        resume_blobs=[pdf],
        silver_resumes=[("cid:a", h)],
    )
    res = client.get("/resume/cid:a")
    assert res.status_code == 200
    assert res.content == pdf


# ---------------------------------------------------------------------------
# Decision capture (append-only; PII-safe)
# ---------------------------------------------------------------------------


def test_decisions_append_pii_safe(make_client: Builder, tmp_path: Path) -> None:
    client = make_client([])
    res = client.post(
        "/decisions",
        json={
            "run_id": "ROLE-Q1",
            "role_id": "ROLE-Q1",
            "reviewer": "web-ui",
            "decisions": [
                {"candidate_id": "cid:a", "action": "forward", "reason": "expert kotlin"}
            ],
        },
    )
    assert res.status_code == 200
    assert res.json()["recorded"] == 1
    log = (tmp_path / "decisions" / "ROLE-Q1.jsonl").read_text(encoding="utf-8")
    assert "cid:a" in log  # keyed by pseudonym
    assert '"action": "forward"' in log


# ---------------------------------------------------------------------------
# Review fixes: missing-blob 404 (not raw 500), and confirm-required gate
# ---------------------------------------------------------------------------


def test_resume_missing_blob_404(wired: None, make_client: Builder) -> None:
    """Gold cites a résumé hash but the bronze blob is absent → clean 404, not an untyped 500."""
    client = make_client(
        [_gold("cid:a", valid_as_of=date(2026, 6, 10), resume_hash="sha256:deadbeef")]
    )  # no resume_blobs seeded
    res = client.get("/resume/cid:a")
    assert res.status_code == 404


def test_match_query_requires_confirm(make_client: Builder) -> None:
    """`/match/query` without ``confirm: true`` → 400; the confirmed role is the gate (AD-110)."""
    client = make_client([])
    res = client.post("/match/query", json={"prose": "Senior Kotlin engineer in Bengaluru"})
    assert res.status_code == 400
