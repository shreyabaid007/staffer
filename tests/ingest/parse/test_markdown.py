"""Tests for Markdown feedback bronze parsing against golden fixtures (a-001 T-008)."""

from pathlib import Path

from dsm.ingest.models import SourceType
from dsm.ingest.parse.markdown import parse_markdown

_FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "ingest"


def _read(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


def test_multi_item_feedback_splits_with_email_key_and_kind() -> None:
    records = parse_markdown(_read("feedback_multi.md"), "sha256:fb", run_id="t")

    assert len(records) == 2
    assert all(r.source_type is SourceType.FEEDBACK for r in records)
    assert all(r.raw["email_key"] == "aarav@example.com" for r in records)
    assert [r.row_index for r in records] == [0, 1]
    assert records[0].raw["kind"] == "project"
    assert records[1].raw["kind"] == "client"  # heading names a client review
    # verbatim item markdown includes its heading
    first_md = records[0].raw["raw_markdown"]
    assert isinstance(first_md, str)
    assert first_md.startswith("## Project Review")
    assert "card-auth rework" in first_md


def test_single_item_feedback() -> None:
    records = parse_markdown(_read("feedback_single.md"), "sha256:fb", run_id="t")
    assert len(records) == 1
    assert records[0].row_index == 0
    assert records[0].raw["email_key"] == "vikram@example.com"
    assert records[0].raw["kind"] == "project"


def test_no_email_key_is_logged_and_skipped() -> None:
    assert parse_markdown(_read("feedback_nokey.md"), "sha256:fb", run_id="t") == []


def test_headingless_body_is_single_item() -> None:
    data = b"email: a@x.com\n\nJust some freeform feedback, no headings."
    records = parse_markdown(data, "sha256:fb", run_id="t")
    assert len(records) == 1
    assert records[0].raw["email_key"] == "a@x.com"
    assert records[0].raw["kind"] == "project"


def test_is_deterministic() -> None:
    data = _read("feedback_multi.md")
    assert parse_markdown(data, "sha256:fb", run_id="t") == parse_markdown(
        data, "sha256:fb", run_id="t"
    )
