"""End-to-end test for `dsm ingest` silver output (a-002 T-008-CLI; CLI-1/2/3)."""

from pathlib import Path

from typer.testing import CliRunner

from dsm.cli.main import app

_runner = CliRunner()

_BEACH = (
    b"Beach - as of 2026-06-01 (synthetic)\n"
    b"Name,Email,Grade,Key Skills,Location,Chennai-open\n"
    b'Priya,priya@acme.example,Lead Consultant,"Java, Kotlin",Bengaluru,Yes\n'
    b"Arjun,arjun@acme.example,Senior Consultant,Python,Pune,No\n"
)
_ROLLING_OFF = (
    b"Rolling Off - as of 2026-06-01 (synthetic)\n"
    b"Name,Email,Roll-off Date,Confidence,Location,Chennai-open\n"
    b"Meera,meera@acme.example,2026-06-20,medium,Chennai,No\n"
    b"Bad,bad@acme.example,soon,low,Pune,No\n"  # invalid date → coercion-skipped
)


def _raw_dir(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    (raw / "supply").mkdir(parents=True)
    (raw / "supply" / "beach.csv").write_bytes(_BEACH)
    (raw / "supply" / "rolling_off.csv").write_bytes(_ROLLING_OFF)
    return raw


def _run(tmp_path: Path):
    return _runner.invoke(
        app,
        [
            "ingest",
            "--raw-dir",
            str(_raw_dir(tmp_path)),
            "--bronze-dir",
            str(tmp_path / "bronze"),
            "--silver-dir",
            str(tmp_path / "silver"),
            "--run-id",
            "run-cli",
        ],
    )


def test_ingest_prints_silver_summary_and_exits_zero(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.exit_code == 0, result.output
    out = result.output
    assert "── Silver ──" in out
    # 2 beach + 1 valid rolling_off = 3 normalized; the bad-date row is skipped (CLI-3).
    assert "normalized      : 3" in out
    assert "coercion-skipped: 1" in out
    assert "free_now=2 rolling_off=1 new_joiner=0" in out


def test_ingest_summary_is_pii_safe(tmp_path: Path) -> None:
    """CLI-2: no raw email / name echoed to stdout — only candidate_id tokens."""
    out = _run(tmp_path).output
    assert "priya@acme.example" not in out
    assert "meera@acme.example" not in out
    assert "cid:" in out  # candidate_id tokens are shown


def test_ingest_persists_silver_layer(tmp_path: Path) -> None:
    _run(tmp_path)
    written = list((tmp_path / "silver" / "records").glob("*.jsonl"))
    assert written, "expected silver records to be written"


def test_ingest_fails_fast_without_candidate_id_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DSM_CANDIDATE_ID_KEY", raising=False)
    result = _run(tmp_path)
    assert result.exit_code == 1
    assert "DSM_CANDIDATE_ID_KEY" in result.output
