"""Tests for PDF bronze parsing (a-001 T-007).

The Docling conversion (``_extract``) is the I/O edge and is monkeypatched so these tests run
offline and deterministically (NF-NONET-1) — they exercise the record-building, email
detection, OCR-fallback, and log+skip logic, not Docling itself.
"""

import dsm.ingest.parse.pdf as pdf_mod
from dsm.ingest.models import SourceType
from dsm.ingest.parse.pdf import _first_email, parse_pdf


def test_first_email_regex() -> None:
    assert _first_email("contact aarav@example.com for details") == "aarav@example.com"
    assert _first_email("no address here") == ""


def test_golden_pdf_record(monkeypatch) -> None:
    def fake_extract(data: bytes, *, ocr: bool) -> tuple[str, list[str]]:
        assert ocr is False  # text PDF: OCR not needed
        return ("Backend engineer. Email aarav@example.com", ["SKILLS", "EXPERIENCE"])

    monkeypatch.setattr(pdf_mod, "_extract", fake_extract)
    records = parse_pdf(b"%PDF-fake", "sha256:cv", run_id="t")

    assert len(records) == 1
    rec = records[0]
    assert rec.source_type is SourceType.RESUME
    assert rec.row_index == 0
    assert rec.raw["sections"] == ["SKILLS", "EXPERIENCE"]
    assert rec.raw["email_found"] == "aarav@example.com"
    assert "Backend engineer" in rec.raw["text"]


def test_pdf_without_email_has_empty_email_found(monkeypatch) -> None:
    monkeypatch.setattr(pdf_mod, "_extract", lambda data, *, ocr: ("resume text only", []))
    records = parse_pdf(b"%PDF", "sha256:cv", run_id="t")
    assert records[0].raw["email_found"] == ""


def test_ocr_fallback_when_direct_text_empty(monkeypatch) -> None:
    def fake_extract(data: bytes, *, ocr: bool) -> tuple[str, list[str]]:
        if not ocr:
            return ("", [])  # direct extraction empty → triggers OCR
        return ("scanned text vikram@example.com", ["SUMMARY"])

    monkeypatch.setattr(pdf_mod, "_extract", fake_extract)
    records = parse_pdf(b"%PDF-scanned", "sha256:scan", run_id="t")

    assert len(records) == 1
    assert records[0].raw["email_found"] == "vikram@example.com"
    assert records[0].raw["sections"] == ["SUMMARY"]


def test_no_text_after_ocr_is_logged_and_skipped(monkeypatch) -> None:
    monkeypatch.setattr(pdf_mod, "_extract", lambda data, *, ocr: ("", []))
    assert parse_pdf(b"%PDF-image", "sha256:img", run_id="t") == []


def test_extraction_error_is_logged_and_skipped(monkeypatch) -> None:
    def boom(data: bytes, *, ocr: bool) -> tuple[str, list[str]]:
        raise ValueError("corrupt pdf")

    monkeypatch.setattr(pdf_mod, "_extract", boom)
    assert parse_pdf(b"not-a-pdf", "sha256:bad", run_id="t") == []
