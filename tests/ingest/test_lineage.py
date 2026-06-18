"""Tests for the lineage seed: run manifest + invalid logging (a-001 T-009)."""

from datetime import datetime

import structlog

from dsm.ingest.lineage import build_run_manifest, log_invalid
from dsm.ingest.models import LandingStatus, ManifestEntry, SourceType


def _entry(status: LandingStatus) -> ManifestEntry:
    return ManifestEntry(
        run_id="run-1",
        source_uri="x",
        source_type=SourceType.SUPPLY_BEACH if status is not LandingStatus.INVALID else None,
        raw_bytes_hash="sha256:h" if status is not LandingStatus.INVALID else None,
        size_bytes=1,
        discovered_at=datetime(2026, 6, 18),
        status=status,
    )


def test_build_run_manifest_tallies_statuses() -> None:
    entries = [
        _entry(LandingStatus.LANDED),
        _entry(LandingStatus.LANDED),
        _entry(LandingStatus.SKIPPED),
        _entry(LandingStatus.INVALID),
    ]
    rm = build_run_manifest("run-1", entries)
    assert (rm.landed, rm.skipped, rm.invalid) == (2, 1, 1)
    assert rm.entries == entries


def test_build_run_manifest_adds_parse_invalid() -> None:
    rm = build_run_manifest("run-1", [_entry(LandingStatus.LANDED)], parse_invalid=3)
    assert rm.landed == 1
    assert rm.invalid == 3  # parse-step skips folded into the invalid count


def test_log_invalid_emits_reason_payload_run_id() -> None:
    with structlog.testing.capture_logs() as logs:
        log_invalid(run_id="run-1", reason="no_email_key", payload="sha256:fb", source_uri="x.md")
    assert len(logs) == 1
    event = logs[0]
    assert event["event"] == "ingest.invalid"
    assert event["log_level"] == "warning"
    assert event["reason"] == "no_email_key"
    assert event["payload"] == "sha256:fb"
    assert event["run_id"] == "run-1"
    assert event["source_uri"] == "x.md"
