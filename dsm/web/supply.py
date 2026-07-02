"""Supply-pool management behind the web routes (c-011; AD-XXY).

The **raw supply CSVs stay the single source of truth** — this module edits inputs
(`data/raw/supply/*.csv`, `data/raw/resumes/`, `data/raw/feedback/`) and the ingest pipeline
(triggered separately, `dsm.web.jobs`) turns them into gold/index state. It reimplements no
pipeline stage: sheet classification reuses ``dsm.ingest.land.classify``; banner dates reuse
``read_banner_date``; feedback linking is validated with the **real** ``parse_markdown``.

PII: rows shown to the operator come from the operator's own sheets (the authorised-human
surface, like the c-008 shortlist). No ``dsm.pii`` import — the ``candidate_id`` join goes
through ``dsm.cli.commands.derive_candidate_id`` (the composition-root seam).
"""

from __future__ import annotations

import csv
import io
import os
import re
from datetime import UTC, date, datetime
from pathlib import Path

import dsm.cli.commands as commands
from dsm.ingest.goldstore import read_gold
from dsm.ingest.land import classify
from dsm.ingest.models import SourceType
from dsm.ingest.parse.csv import read_banner_date
from dsm.ingest.parse.markdown import parse_markdown
from dsm.web.models import (
    AddCandidateRequest,
    AttachmentResponse,
    Category,
    SupplyResponse,
    SupplyRowView,
    SupplySheetView,
)
from dsm.web.service import WebServiceError, _resume_hashes

_CATEGORY_TO_SOURCE: dict[Category, SourceType] = {
    "beach": SourceType.SUPPLY_BEACH,
    "rolling_off": SourceType.SUPPLY_ROLLING_OFF,
    "new_joiner": SourceType.SUPPLY_NEW_JOINERS,
}
# Header written when a category sheet does not exist yet — mirrors the real data's schema.
_DEFAULT_HEADERS: dict[Category, list[str]] = {
    "beach": [
        "#",
        "Name",
        "Email",
        "Grade",
        "Key Skills",
        "Location",
        "Chennai-open",
        "Days on Beach",
        "Notes",
    ],
    "rolling_off": [
        "#",
        "Name",
        "Email",
        "Grade",
        "Key Skills",
        "Current Client",
        "Roll-off Date",
        "Confidence",
        "Location",
        "Chennai-open",
        "Notes",
    ],
    "new_joiner": [
        "#",
        "Name",
        "Email",
        "Grade",
        "Key Skills (from CV)",
        "Join Date",
        "Location",
        "Chennai-open",
        "Notes",
    ],
}
_DEFAULT_FILENAMES: dict[Category, str] = {
    "beach": "Beach.csv",
    "rolling_off": "Rolling Off.csv",
    "new_joiner": "New Joiners.csv",
}
_BANNER_LABELS: dict[Category, str] = {
    "beach": "Beach",
    "rolling_off": "Rolling Off",
    "new_joiner": "New Joiners",
}
_AS_OF_RE = re.compile(r"as of \d{4}-\d{2}-\d{2}")
_SAFE_FILE_RE = re.compile(r"^[A-Za-z0-9 ._-]+$")


class DuplicateCandidate(WebServiceError):
    status_code = 409


class CandidateRowNotFound(WebServiceError):
    status_code = 404


class InvalidUpload(WebServiceError):
    status_code = 422


class AttachmentNotFound(WebServiceError):
    status_code = 404


# ---------------------------------------------------------------------------
# Sheet I/O — banner + header preserved, atomic writes
# ---------------------------------------------------------------------------


def _sheet_path(raw_dir: Path, category: Category) -> Path:
    """The category's CSV under ``raw/supply`` (existing file wins; else the default name)."""
    supply = raw_dir / "supply"
    target = _CATEGORY_TO_SOURCE[category]
    if supply.is_dir():
        for path in sorted(supply.glob("*.csv")):
            if classify(path) is target:
                return path
    return supply / _DEFAULT_FILENAMES[category]


def _read_sheet(path: Path, category: Category) -> tuple[str, list[str], list[list[str]]]:
    """Split a sheet into (banner line, header cells, body rows). Creates nothing."""
    if not path.is_file():
        header = _DEFAULT_HEADERS[category]
        return _banner_line(category, len(header)), header, []
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if not lines:
        header = _DEFAULT_HEADERS[category]
        return _banner_line(category, len(header)), header, []
    banner = lines[0]
    reader = list(csv.reader(io.StringIO("\n".join(lines[1:]))))
    header = reader[0] if reader else _DEFAULT_HEADERS[category]
    rows = [r for r in reader[1:] if any(cell.strip() for cell in r)]
    return banner, header, rows


def _banner_line(category: Category, width: int) -> str:
    today = date.today().isoformat()
    label = _BANNER_LABELS[category]
    return f"{label} - as of {today} (via web)" + "," * max(0, width - 1)


def _bump_banner(banner: str, category: Category, width: int) -> str:
    """Refresh the ``as of`` date in-place; fall back to a fresh banner when none is present."""
    today = date.today().isoformat()
    if _AS_OF_RE.search(banner):
        return _AS_OF_RE.sub(f"as of {today}", banner)
    return _banner_line(category, width)


def _write_sheet(path: Path, banner: str, header: list[str], rows: list[list[str]]) -> None:
    """Atomic temp+rename write (NF-4), banner first, header + body via the csv writer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(row + [""] * (len(header) - len(row)) if len(row) < len(header) else row)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(banner + "\n" + buf.getvalue(), encoding="utf-8")
    os.replace(tmp, path)


def _col(header: list[str], *needles: str) -> int | None:
    """Index of the first header cell containing any needle (case-insensitive), else None."""
    lowered = [h.strip().lower() for h in header]
    for needle in needles:
        for i, cell in enumerate(lowered):
            if needle in cell:
                return i
    return None


def _cell(row: list[str], idx: int | None) -> str:
    return row[idx].strip() if idx is not None and idx < len(row) else ""


# ---------------------------------------------------------------------------
# Read — GET /supply
# ---------------------------------------------------------------------------


def _feedback_files_by_email(raw_dir: Path) -> dict[str, list[str]]:
    """Map candidate email → linked feedback filenames, using the REAL markdown parser's key."""
    out: dict[str, list[str]] = {}
    fb_dir = raw_dir / "feedback"
    if not fb_dir.is_dir():
        return out
    for path in sorted(fb_dir.glob("*.md")):
        try:
            records = parse_markdown(path.read_bytes(), "sha256:web-scan", run_id="web-scan")
        except Exception:  # noqa: BLE001 — an unreadable file simply doesn't link
            continue
        if records:
            email = str(records[0].raw.get("email_key", "")).lower()
            if email:
                out.setdefault(email, []).append(path.name)
    return out


def _resume_file_for(raw_dir: Path, email: str) -> str | None:
    """The web-conventional resume file for an email, if present (name-based, upload path)."""
    path = raw_dir / "resumes" / _resume_filename(email)
    return path.name if path.is_file() else None


def _row_view(
    category: Category,
    header: list[str],
    row: list[str],
    *,
    raw_dir: Path,
    gold_dir: Path,
    fb_by_email: dict[str, list[str]],
) -> SupplyRowView | None:
    email = _cell(row, _col(header, "email"))
    if not email:
        return None
    cid = commands.derive_candidate_id(email)
    gold = read_gold(cid, gold_dir)
    skills_raw = _cell(row, _col(header, "skill"))
    fb_files = fb_by_email.get(email.lower(), [])
    has_resume_ingested = bool(_resume_hashes(cid, gold_dir))
    return SupplyRowView(
        candidate_id=cid,
        name=_cell(row, _col(header, "name")),
        email=email,
        grade=_cell(row, _col(header, "grade")) or None,
        skills=[s.strip() for s in skills_raw.split(",") if s.strip()],
        location=_cell(row, _col(header, "location")) or None,
        chennai_open=_cell(row, _col(header, "chennai")).lower() == "yes",
        category=category,
        roll_off_date=_cell(row, _col(header, "roll-off", "roll off")) or None,
        confidence=_cell(row, _col(header, "confidence")).lower() or None,
        join_date=_cell(row, _col(header, "join date")) or None,
        days_on_beach=int(d)
        if (d := _cell(row, _col(header, "days on beach"))).isdigit()
        else None,
        notes=_cell(row, _col(header, "notes")) or None,
        ingested=gold is not None and not gold.is_tombstoned,
        has_resume=has_resume_ingested or _resume_file_for(raw_dir, email) is not None,
        feedback_count=len(gold.feedback) if gold is not None else 0,
        feedback_files=fb_files,
    )


def read_supply(*, raw_dir: Path, gold_dir: Path) -> SupplyResponse:
    """``GET /supply`` — all three sheets from the current raw CSVs + gold-derived sync status."""
    fb_by_email = _feedback_files_by_email(raw_dir)
    sheets: list[SupplySheetView] = []
    for category in ("beach", "rolling_off", "new_joiner"):
        path = _sheet_path(raw_dir, category)
        skipped: list[str] = []
        rows_out: list[SupplyRowView] = []
        as_of: str | None = None
        if path.is_file():
            as_of_date = read_banner_date(path.read_bytes())
            as_of = as_of_date.isoformat() if as_of_date else None
            _banner, header, rows = _read_sheet(path, category)
            for i, row in enumerate(rows):
                view = _row_view(
                    category,
                    header,
                    row,
                    raw_dir=raw_dir,
                    gold_dir=gold_dir,
                    fb_by_email=fb_by_email,
                )
                if view is None:
                    skipped.append(f"{path.name} row {i + 1}: no email — skipped")
                    continue
                rows_out.append(view)
        sheets.append(
            SupplySheetView(category=category, as_of=as_of, rows=rows_out, skipped=skipped)
        )
    return SupplyResponse(sheets=sheets)


# ---------------------------------------------------------------------------
# Mutate — POST /supply/candidates, DELETE /supply/candidates/{category}/{email}
# ---------------------------------------------------------------------------


def _request_to_row(req: AddCandidateRequest, header: list[str], row_number: int) -> list[str]:
    """Map the typed request onto the sheet's own column order (unknown columns stay empty)."""
    values: dict[int, str] = {}

    def put(idx: int | None, value: str) -> None:
        if idx is not None:
            values[idx] = value

    put(_col(header, "#"), str(row_number))
    put(_col(header, "name"), req.name)
    put(_col(header, "email"), str(req.email))
    put(_col(header, "grade"), req.grade or "")
    put(_col(header, "skill"), ", ".join(req.skills))
    put(_col(header, "location"), req.location or "")
    put(_col(header, "chennai"), "Yes" if req.chennai_open else "No")
    put(_col(header, "notes"), req.notes or "")
    if req.category == "rolling_off":
        put(
            _col(header, "roll-off", "roll off"),
            req.roll_off_date.isoformat() if req.roll_off_date else "",
        )
        put(_col(header, "confidence"), req.confidence or "")
    if req.category == "new_joiner":
        put(_col(header, "join date"), req.join_date.isoformat() if req.join_date else "")
    if req.category == "beach":
        put(_col(header, "days on beach"), "0")
    return [values.get(i, "") for i in range(len(header))]


def add_candidate(req: AddCandidateRequest, *, raw_dir: Path, gold_dir: Path) -> SupplyRowView:
    """Append a row to the category sheet (banner bumped, atomic). 409 on a duplicate email."""
    path = _sheet_path(raw_dir, req.category)
    banner, header, rows = _read_sheet(path, req.category)
    email_idx = _col(header, "email")
    lowered = str(req.email).lower()
    if any(_cell(r, email_idx).lower() == lowered for r in rows):
        raise DuplicateCandidate(
            f"A {req.category} row with this email already exists — remove it first."
        )
    rows.append(_request_to_row(req, header, row_number=len(rows) + 1))
    _write_sheet(path, _bump_banner(banner, req.category, len(header)), header, rows)
    view = _row_view(
        req.category,
        header,
        rows[-1],
        raw_dir=raw_dir,
        gold_dir=gold_dir,
        fb_by_email=_feedback_files_by_email(raw_dir),
    )
    assert view is not None  # the request model guarantees an email
    return view


def remove_candidate(category: Category, email: str, *, raw_dir: Path) -> None:
    """Delete the row matching ``email`` (case-insensitive) from the category sheet."""
    path = _sheet_path(raw_dir, category)
    banner, header, rows = _read_sheet(path, category)
    email_idx = _col(header, "email")
    lowered = email.strip().lower()
    kept = [r for r in rows if _cell(r, email_idx).lower() != lowered]
    if len(kept) == len(rows):
        raise CandidateRowNotFound(f"No {category} row with that email.")
    _write_sheet(path, _bump_banner(banner, category, len(header)), header, kept)


# ---------------------------------------------------------------------------
# Attachments — resume PDF + feedback markdown
# ---------------------------------------------------------------------------


def _email_local(email: str) -> str:
    local = email.split("@", 1)[0]
    return re.sub(r"[^A-Za-z0-9._-]+", "_", local) or "candidate"


def _resume_filename(email: str) -> str:
    return f"{_email_local(email)}.pdf"


def store_resume(email: str, data: bytes, *, raw_dir: Path) -> AttachmentResponse:
    """Write the resume PDF (replace-on-re-upload) + report whether it will link at ingest.

    Linking is by the first email found in the PDF **text** (``parse/pdf.py``), so we check:
    a cheap raw-bytes scan first, then real text extraction (lazy import — Docling is heavy),
    and report ``unknown`` when extraction itself fails (ingest may still OCR it).
    """
    if not data.startswith(b"%PDF"):
        raise InvalidUpload("Resume must be a PDF file.")
    dest_dir = raw_dir / "resumes"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / _resume_filename(email)
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)

    link_check = "no_email_found"
    if email.lower().encode() in data.lower():
        link_check = "ok"
    else:
        try:
            from dsm.ingest.parse.pdf import parse_pdf  # lazy: heavy import, upload-time only

            records = parse_pdf(data, "sha256:web-linkcheck", run_id="web-linkcheck")
            if records and str(records[0].raw.get("email_found", "")).lower() == email.lower():
                link_check = "ok"
        except Exception:  # noqa: BLE001 — extraction failure ≠ upload failure
            link_check = "unknown"
    return AttachmentResponse(stored=dest.name, link_check=link_check)


def delete_resume(email: str, *, raw_dir: Path) -> None:
    path = raw_dir / "resumes" / _resume_filename(email)
    if not path.is_file():
        raise AttachmentNotFound("No web-uploaded resume on file for this candidate.")
    path.unlink()


def _ensure_linked_markdown(email: str, text: str, source: str) -> str:
    """Guarantee the feedback markdown links + splits: an ``email:`` key line + a ``##`` heading.

    Mirrors ``parse_markdown``'s rules — an explicit ``email:`` line wins as the key; items
    split on ``##`` headings; a heading containing "client" is kind=client.
    """
    body = text.strip()
    kind = "Client" if source == "client" else "Project"
    if not re.search(r"^\s*email:\s*\S+@\S+\s*$", body, re.IGNORECASE | re.MULTILINE):
        body = f"email: {email}\n\n{body}"
    if not re.search(r"(?m)^##\s", body):
        today = date.today().isoformat()
        key_line, _, rest = body.partition("\n")
        body = f"{key_line}\n\n## {kind} feedback - web - {today}\n\n{rest.strip()}"
    return body + "\n"


def store_feedback(email: str, text: str, *, source: str, raw_dir: Path) -> AttachmentResponse:
    """Write one feedback markdown file (append semantics — a new file per entry, never
    overwriting an earlier one) and verify with the real parser that it links to ``email``."""
    if not text.strip():
        raise InvalidUpload("Feedback is empty.")
    content = _ensure_linked_markdown(email, text, source)
    records = parse_markdown(content.encode("utf-8"), "sha256:web-verify", run_id="web-verify")
    if not records or str(records[0].raw.get("email_key", "")).lower() != email.lower():
        # Payload contains a different email earlier in the text than our key line can win over.
        raise InvalidUpload(
            "Feedback text contains a conflicting email address — it would link to the wrong "
            "candidate. Remove the other address or put it after an explicit 'email:' line."
        )
    dest_dir = raw_dir / "feedback"
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"{_email_local(email)}-web-{stamp}.md"
    n = 1
    while dest.exists():  # same-second uploads: suffix rather than overwrite (append semantics)
        n += 1
        dest = dest_dir / f"{_email_local(email)}-web-{stamp}-{n}.md"
    dest.write_text(content, encoding="utf-8")
    return AttachmentResponse(stored=dest.name, link_check="ok")


def delete_feedback(email: str, filename: str, *, raw_dir: Path) -> None:
    """Delete one feedback file — traversal-guarded and only if it links to this candidate."""
    if not _SAFE_FILE_RE.match(filename) or "/" in filename or "\\" in filename:
        raise InvalidUpload("Invalid feedback filename.")
    fb_dir = (raw_dir / "feedback").resolve()
    path = (fb_dir / filename).resolve()
    if path.parent != fb_dir or not path.is_file():
        raise AttachmentNotFound("No such feedback file.")
    records = parse_markdown(path.read_bytes(), "sha256:web-delete", run_id="web-delete")
    if not records or str(records[0].raw.get("email_key", "")).lower() != email.lower():
        raise AttachmentNotFound("That feedback file is not linked to this candidate.")
    path.unlink()
