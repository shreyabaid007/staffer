"""End-to-end test for the CLI match command."""

import json
import subprocess


def test_match_command_runs_end_to_end() -> None:
    """Verify `dsm match` exits 0 and returns valid JSON."""
    result = subprocess.run(
        ["uv", "run", "dsm", "match", "--role-id", "ROLE-STUB-01"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert "role_id" in output
    assert "ranked_assessments" in output
