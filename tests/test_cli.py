"""End-to-end test for the CLI match command."""

import json
import os
import subprocess


def test_match_command_runs_end_to_end() -> None:
    """Verify `dsm match` exits 0 and returns valid JSON (LM stubbed via DSM_STUB_LM)."""
    env = {**os.environ, "DSM_STUB_LM": "1"}
    result = subprocess.run(
        ["uv", "run", "dsm", "match", "--role-id", "ROLE-STUB-01"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert "role_id" in output
    assert "ranked_assessments" in output
