"""Step 2 — Parse (PDF): Docling → section-tagged text + email_found, OCR fallback.

Deterministic and LLM-free: this is verbatim text extraction, not enrichment. The Docling
conversion is isolated in ``_extract`` (the I/O edge) so the record-building logic is testable
offline. A PDF that yields no text even after the OCR fallback is logged, skipped, and counted
(P-INVALID-1).
"""

from __future__ import annotations

import io
import re

from dsm.ingest.lineage import log_invalid
from dsm.ingest.models import BronzeRecord, SourceType

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _first_email(text: str) -> str:
    """First email address in ``text`` by deterministic regex, or ``""`` if none (P-EMAIL-1)."""
    m = _EMAIL_RE.search(text)
    return m.group(0) if m else ""


def _extract(data: bytes, *, ocr: bool) -> tuple[str, list[str]]:
    """Run Docling over PDF bytes → (full text, section/heading tags). The I/O edge.

    Imports are function-local so the heavy Docling import is paid only when a PDF is parsed.
    """
    from docling.datamodel.base_models import DocumentStream, InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc.labels import DocItemLabel

    options = PdfPipelineOptions()
    options.do_ocr = ocr
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )
    doc = converter.convert(DocumentStream(name="resume.pdf", stream=io.BytesIO(data))).document
    text = doc.export_to_markdown()
    sections = [
        t.text
        for t in doc.texts
        if getattr(t, "label", None) in (DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE)
    ]
    return text, sections


def parse_pdf(data: bytes, source_hash: str, *, run_id: str) -> list[BronzeRecord]:
    """Parse a resume PDF blob into a single verbatim ``BronzeRecord`` (or log+skip)."""
    try:
        text, sections = _extract(data, ocr=False)
        if not text.strip():
            text, sections = _extract(data, ocr=True)  # OCR fallback (P-OCR-1)
    except Exception as exc:  # Docling raises a range of errors on unreadable input
        log_invalid(
            run_id=run_id,
            reason=f"extraction_error: {exc!r}",
            payload=source_hash,
            source_uri=source_hash,
        )
        return []

    if not text.strip():
        log_invalid(
            run_id=run_id,
            reason="no_text_after_ocr",
            payload=source_hash,
            source_uri=source_hash,
        )
        return []

    return [
        BronzeRecord(
            source_hash=source_hash,
            source_type=SourceType.RESUME,
            row_index=0,
            raw={"text": text, "sections": sections, "email_found": _first_email(text)},
        )
    ]
