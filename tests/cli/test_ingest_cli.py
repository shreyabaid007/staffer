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
    (raw / "supply").mkdir(parents=True, exist_ok=True)
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
            "--gold-dir",
            str(tmp_path / "gold"),
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


def test_ingest_prints_gold_summary_and_exits_zero(tmp_path: Path) -> None:
    """T-012/CLI: supply-only run merges to thin gold (no enrich) and prints the Gold summary."""
    result = _run(tmp_path)
    assert result.exit_code == 0, result.output
    out = result.output
    assert "── Gold ──" in out
    # 3 candidates (Priya, Arjun, Meera) — all thin (CSV-only, no resume/feedback).
    assert "entities    : 3" in out
    assert "thin=3 medium=0 rich=0" in out
    assert "leak-scan hits: 0" in out


def test_gold_summary_is_pii_safe(tmp_path: Path) -> None:
    """case 23: no raw name/email reaches stdout — only candidate_id tokens + structured fields."""
    out = _run(tmp_path).output
    for leaked in ("Priya", "Arjun", "Meera", "priya@acme.example", "@acme.example"):
        assert leaked not in out
    assert "cid:" in out


def test_gold_layer_persisted(tmp_path: Path) -> None:
    """GS-1: one gold/<cid>.json per candidate is written to the gold dir."""
    _run(tmp_path)
    written = list((tmp_path / "gold").glob("*.json"))
    assert len(written) == 3


def test_ingest_persists_identity_vault(tmp_path: Path) -> None:
    """R-06: ingest writes a gitignored FileVault (beside gold) the query plane can read back."""
    from dsm.pii.vault import FileVault
    from dsm.pii.vault import candidate_id as derive_cid

    result = _run(tmp_path)
    assert result.exit_code == 0, result.output

    vault_file = tmp_path / "identity" / "vault.json"
    assert vault_file.exists()
    # A separate FileVault instance (≈ the query process) reads the seeded identities back.
    vault = FileVault(vault_file)
    assert vault.get_identity(derive_cid("priya@acme.example")) == ("Priya", "priya@acme.example")
    assert vault.get_identity(derive_cid("meera@acme.example")) == ("Meera", "meera@acme.example")


def test_ingest_vault_file_is_the_only_place_raw_identity_lives(tmp_path: Path) -> None:
    """R-06: gold keeps vault REFS only — raw name/email never enters gold/<cid>.json."""
    _run(tmp_path)
    gold_text = "\n".join(p.read_text() for p in (tmp_path / "gold").glob("*.json"))
    for leaked in ("Priya", "priya@acme.example", "Meera", "meera@acme.example"):
        assert leaked not in gold_text
    assert "name_vault_ref" in gold_text and "email_vault_ref" in gold_text


def test_idempotent_rerun_does_not_tombstone(tmp_path: Path) -> None:
    """Regression: a re-run lands everything SKIPPED → 0 candidates processed; it must leave gold
    UNCHANGED, never tombstone the prior set (RC-1 guard)."""
    import json

    first = _run(tmp_path)
    assert first.exit_code == 0, first.output

    second = _run(tmp_path)  # same tmp_path → manifest already has every hash → all SKIPPED
    assert second.exit_code == 0, second.output
    assert "gold left unchanged" in second.output
    assert "tombstones  : 3" not in second.output

    for path in (tmp_path / "gold").glob("*.json"):
        assert json.loads(path.read_text())["is_tombstoned"] is False


def test_removing_one_supply_row_tombstones_only_that_candidate(tmp_path: Path) -> None:
    """Regression (AD-093): editing ONE supply file must tombstone only the row removed from it —
    not every candidate whose (unchanged) supply file was SKIPPED at landing. Reconcile diffs
    against the full current supply roster, not just files re-landed this run."""
    import json

    from dsm.pii.vault import candidate_id

    raw = tmp_path / "raw"
    (raw / "supply").mkdir(parents=True, exist_ok=True)
    (raw / "supply" / "beach.csv").write_bytes(_BEACH)
    (raw / "supply" / "rolling_off.csv").write_bytes(_ROLLING_OFF)

    def _ingest():
        return _runner.invoke(
            app,
            [
                "ingest",
                "--raw-dir",
                str(raw),
                "--bronze-dir",
                str(tmp_path / "bronze"),
                "--silver-dir",
                str(tmp_path / "silver"),
                "--gold-dir",
                str(tmp_path / "gold"),
                "--run-id",
                "run-cli",
            ],
        )

    first = _ingest()
    assert first.exit_code == 0, first.output

    # Remove Arjun from beach.csv; leave rolling_off.csv byte-identical (→ SKIPPED at landing).
    (raw / "supply" / "beach.csv").write_bytes(
        b"Beach - as of 2026-06-01 (synthetic)\n"
        b"Name,Email,Grade,Key Skills,Location,Chennai-open\n"
        b'Priya,priya@acme.example,Lead Consultant,"Java, Kotlin",Bengaluru,Yes\n'
    )

    second = _ingest()
    assert second.exit_code == 0, second.output
    assert "tombstones  : 1" in second.output  # ONLY Arjun — not Meera (her file was SKIPPED)

    tombstoned = {
        json.loads(p.read_text())["candidate_id"]
        for p in (tmp_path / "gold").glob("*.json")
        if json.loads(p.read_text())["is_tombstoned"]
    }
    assert tombstoned == {candidate_id("arjun@acme.example")}
    assert candidate_id("meera@acme.example") not in tombstoned
    assert candidate_id("priya@acme.example") not in tombstoned


def test_readding_supply_row_revives_tombstoned_candidate(tmp_path: Path) -> None:
    """Re-adding a previously-tombstoned candidate to a supply file revives them: the fresh merge
    overwrites the tombstone with a live (is_tombstoned=False) gold record, and they are not
    re-tombstoned."""
    import json

    from dsm.pii.vault import candidate_id

    raw = tmp_path / "raw"
    (raw / "supply").mkdir(parents=True, exist_ok=True)
    (raw / "supply" / "rolling_off.csv").write_bytes(_ROLLING_OFF)
    beach_path = raw / "supply" / "beach.csv"

    def _ingest():
        return _runner.invoke(
            app,
            [
                "ingest",
                "--raw-dir",
                str(raw),
                "--bronze-dir",
                str(tmp_path / "bronze"),
                "--silver-dir",
                str(tmp_path / "silver"),
                "--gold-dir",
                str(tmp_path / "gold"),
                "--run-id",
                "run-cli",
            ],
        )

    arjun_cid = candidate_id("arjun@acme.example")
    arjun_path = tmp_path / "gold" / f"{arjun_cid.removeprefix('cid:')}.json"
    beach_without_arjun = (
        b"Beach - as of 2026-06-01 (synthetic)\n"
        b"Name,Email,Grade,Key Skills,Location,Chennai-open\n"
        b'Priya,priya@acme.example,Lead Consultant,"Java, Kotlin",Bengaluru,Yes\n'
    )

    # 1) Arjun present → live.
    beach_path.write_bytes(_BEACH)
    assert _ingest().exit_code == 0
    assert json.loads(arjun_path.read_text())["is_tombstoned"] is False

    # 2) Arjun removed → tombstoned.
    beach_path.write_bytes(beach_without_arjun)
    assert _ingest().exit_code == 0
    assert json.loads(arjun_path.read_text())["is_tombstoned"] is True

    # 3) Arjun re-added → revived (live again), not re-tombstoned.
    beach_path.write_bytes(_BEACH)
    third = _ingest()
    assert third.exit_code == 0, third.output
    assert "tombstones  : 0" in third.output
    assert json.loads(arjun_path.read_text())["is_tombstoned"] is False
