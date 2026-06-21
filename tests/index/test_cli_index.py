"""End-to-end test for `dsm index` (a-005 T-006; IDX-9; AC-7).

Monkeypatches the Modal embed client to a no-network FakeEmbedClient; seeds a tmp gold dir + a tmp
Milvus Lite db. Asserts the PII-safe ``── Index ──`` summary and exit code 0.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from dsm.cli.main import app
from dsm.ingest.goldstore import write_gold
from dsm.ingest.models import Confidence, GoldCandidate, Grade, MergedSkill, Sourced
from dsm.models import FreeNow, Location
from tests.index.fakes import FakeEmbedClient

_runner = CliRunner()
_SECRET_NAME = "Priya Sharma"


def _gold(
    cid: str,
    *,
    grade: Grade | None = Grade.LEAD_CONSULTANT,
    domains: list[str] | None = None,
    is_tombstoned: bool = False,
) -> GoldCandidate:
    return GoldCandidate(
        candidate_id=cid,
        name_vault_ref=_SECRET_NAME,  # name-shaped sentinel — must never reach stdout
        email_vault_ref="priya@acme.example",
        grade=Sourced(value=grade) if grade is not None else None,
        location=Sourced(value=Location(city="Chennai")),
        availability=Sourced(value=FreeNow()),
        skills=[MergedSkill(name="kotlin", confidence=Confidence.MEDIUM)],
        domains=[Sourced(value=d) for d in (domains or [])],
        is_tombstoned=is_tombstoned,
        gold_hash=f"sha256:{cid}",
        merge_version="merge-v1",
        prompt_version="enrich-v1",
        model_version="anthropic/claude-sonnet-4-6",
    )


@pytest.fixture(autouse=True)
def _fake_embed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("dsm.cli.commands._build_embed_client", lambda: FakeEmbedClient())


def _seed(tmp_path: Path) -> Path:
    gold_dir = tmp_path / "gold"
    write_gold(_gold("cid:good", domains=["payments"]), gold_dir)
    write_gold(_gold("cid:thin", grade=None), gold_dir)
    write_gold(_gold("cid:gone", is_tombstoned=True), gold_dir)
    return gold_dir


def _run(tmp_path: Path):
    return _runner.invoke(
        app,
        [
            "index",
            "--gold-dir",
            str(_seed(tmp_path)),
            "--db-path",
            str(tmp_path / "index" / "milvus.db"),
            "--run-id",
            "run-cli",
        ],
    )


def test_index_prints_summary_and_exits_zero(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.exit_code == 0, result.output
    out = result.output
    assert "── Index ──" in out
    assert "indexed           : 1" in out
    assert "thin-skipped      : 1" in out
    assert "tombstoned-removed: 1" in out
    assert "skipped-unchanged : 0" in out


def test_index_summary_is_pii_safe(tmp_path: Path) -> None:
    """IDX-9: candidate_id tokens + structured fields only — no name/email/embed_text."""
    out = _run(tmp_path).output
    assert _SECRET_NAME not in out
    assert "Priya" not in out
    assert "priya@acme.example" not in out
    assert "payments" not in out  # embed_text content (domains prefix) never echoed
    assert "Domains:" not in out
    assert "cid:good" in out  # candidate_id tokens are shown


def test_index_rerun_skips_unchanged(tmp_path: Path) -> None:
    """AC-5 via CLI: a second run over the same gold + db re-embeds nothing."""
    gold_dir = tmp_path / "gold"
    write_gold(_gold("cid:good", domains=["payments"]), gold_dir)
    db = str(tmp_path / "index" / "milvus.db")
    args = ["index", "--gold-dir", str(gold_dir), "--db-path", db, "--run-id", "r"]

    first = _runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    assert "indexed           : 1" in first.output

    second = _runner.invoke(app, args)
    assert second.exit_code == 0, second.output
    assert "indexed           : 0" in second.output
    assert "skipped-unchanged : 1" in second.output
