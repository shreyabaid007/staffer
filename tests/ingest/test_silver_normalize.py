"""Tests for silver record assembly (a-002 T-006; AV-1..5, TX-3, ID-4)."""

from datetime import date

from dsm.ingest.models import BronzeRecord, SourceType
from dsm.ingest.silver import normalize
from dsm.ingest.taxonomy import Taxonomy
from dsm.models import FreeNow, NewJoiner, RollingOff

_TAX = Taxonomy({"java": "java", "kotlin": "kotlin", "python": "python"})
_SNAP = date(2026, 6, 1)


def _beach_row(**over: str) -> BronzeRecord:
    raw: dict[str, str | list[str]] = {
        "Name": "Priya",
        "Email": "priya@acme.example",
        "Grade": "Lead Consultant",
        "Key Skills": "Java, Kotlin",
        "Location": "Bengaluru",
        "Chennai-open": "Yes",
    }
    raw.update(over)
    return BronzeRecord(
        source_hash="sha256:beach", source_type=SourceType.SUPPLY_BEACH, row_index=1, raw=raw
    )


def _norm(record: BronzeRecord) -> object:
    return normalize(record, snapshot_date=_SNAP, taxonomy=_TAX, run_id="run-1")


def test_beach_maps_to_free_now() -> None:
    rec = _norm(_beach_row())
    assert rec is not None
    assert isinstance(rec.availability, FreeNow)  # type: ignore[attr-defined]
    assert rec.valid_as_of == _SNAP  # type: ignore[attr-defined]
    assert rec.candidate_id.startswith("cid:")  # type: ignore[attr-defined]
    assert all(not s.unverified for s in rec.skills)  # type: ignore[attr-defined]


def test_rolling_off_maps_to_rolling_off() -> None:
    raw: dict[str, str | list[str]] = {
        "Email": "p@acme.example",
        "Roll-off Date": "2026-06-20",
        "Confidence": "medium",
        "Location": "Pune",
        "Chennai-open": "No",
    }
    rec = normalize(
        BronzeRecord(
            source_hash="sha256:ro",
            source_type=SourceType.SUPPLY_ROLLING_OFF,
            row_index=1,
            raw=raw,
        ),
        snapshot_date=_SNAP,
        taxonomy=_TAX,
        run_id="run-1",
    )
    assert rec is not None
    assert isinstance(rec.availability, RollingOff)
    assert rec.availability.expected_date == date(2026, 6, 20)
    assert rec.availability.confidence == "medium"


def test_rolling_off_bad_date_is_skipped() -> None:
    raw: dict[str, str | list[str]] = {"Email": "p@acme.example", "Roll-off Date": "soon"}
    rec = normalize(
        BronzeRecord(
            source_hash="sha256:ro",
            source_type=SourceType.SUPPLY_ROLLING_OFF,
            row_index=2,
            raw=raw,
        ),
        snapshot_date=_SNAP,
        taxonomy=_TAX,
        run_id="run-1",
    )
    assert rec is None  # AV-4 log+skip+count


def test_new_joiner_skills_are_unverified() -> None:
    raw: dict[str, str | list[str]] = {
        "Email": "nj@acme.example",
        "Join Date": "2026-07-01",
        "Key Skills (from CV)": "Python, Java",
        "Location": "Chennai",
        "Chennai-open": "No",
    }
    rec = normalize(
        BronzeRecord(
            source_hash="sha256:nj",
            source_type=SourceType.SUPPLY_NEW_JOINERS,
            row_index=1,
            raw=raw,
        ),
        snapshot_date=_SNAP,
        taxonomy=_TAX,
        run_id="run-1",
    )
    assert rec is not None
    assert isinstance(rec.availability, NewJoiner)
    assert rec.availability.join_date == date(2026, 7, 1)
    assert rec.skills and all(s.unverified for s in rec.skills)  # AD-032 / TX-3


def test_resume_has_raw_text_and_no_availability() -> None:
    raw: dict[str, str | list[str]] = {
        "text": "Priya is a backend engineer.",
        "sections": ["SKILLS"],
        "email_found": "priya@acme.example",
    }
    rec = normalize(
        BronzeRecord(source_hash="sha256:cv", source_type=SourceType.RESUME, row_index=0, raw=raw),
        snapshot_date=None,
        taxonomy=_TAX,
        run_id="run-1",
    )
    assert rec is not None
    assert rec.availability is None  # AV-5
    assert rec.raw_text == "Priya is a backend engineer."
    assert rec.candidate_id.startswith("cid:")


def test_feedback_uses_email_key_and_raw_markdown() -> None:
    raw: dict[str, str | list[str]] = {
        "email_key": "priya@acme.example",
        "raw_markdown": "Great on delivery.",
        "kind": "project",
    }
    rec = normalize(
        BronzeRecord(
            source_hash="sha256:fb", source_type=SourceType.FEEDBACK, row_index=0, raw=raw
        ),
        snapshot_date=None,
        taxonomy=_TAX,
        run_id="run-1",
    )
    assert rec is not None
    assert rec.availability is None
    assert rec.raw_text == "Great on delivery."


def test_missing_email_is_skipped() -> None:
    rec = _norm(_beach_row(Email=""))
    assert rec is None  # ID-4


def test_unmapped_skill_is_flagged() -> None:
    rec = _norm(_beach_row(**{"Key Skills": "Java, Cobol"}))
    assert rec is not None
    by_name = {s.name: s for s in rec.skills}  # type: ignore[attr-defined]
    assert by_name["java"].unmapped is False
    assert by_name["Cobol"].unmapped is True


def test_chennai_open_sets_remote_eligible_with_warning() -> None:
    rec = _norm(_beach_row())  # Chennai-open=Yes
    assert rec is not None
    assert rec.location is not None and rec.location.remote_eligible is True  # type: ignore[attr-defined]
    assert any("Chennai-open" in w for w in rec.parse_warnings)  # type: ignore[attr-defined]
