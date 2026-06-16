# a-001-ingest-sheets — Design

> Implements the requirements above. Reflects the **signed-off decisions** (2026-06-16):
> OQ-1 `source=NEW_JOINER`, OQ-2 INTERMEDIATE default, OQ-3 raw skills→`description`,
> OQ-4 declare openpyxl, OQ-5 return a 3-tuple (no `IngestResult` wrapper).

## Modules touched
- **New:** `dsm/ingest/sheets.py` — the real ingestion (public entry point + pure helpers).
- **New:** `dsm/ingest/models.py` — module-local typed contracts (`docs/structure.md`:
  module-local Pydantic lives in `<module>/models.py`).
- **New tests:** `tests/ingest/test_sheets.py`, `tests/ingest/test_parsing.py`, plus a small
  synthetic fixture workbook builder in `tests/ingest/conftest.py`.
- **Deleted:** `dsm/ingest/stub.py` is **superseded**, not yet removed — `dsm/cli/commands.py`
  still imports it (Lane C). Keep the stub until Lane C re-points the orchestrator; this spec
  does **not** touch `cli/`. (Recorded as a Lane C handoff, see tasks T-006.)
- **Config:** `pyproject.toml` gains `openpyxl>=3.1` as a declared dependency (OQ-4).

## Data contracts (Pydantic)

### Shared (frozen, AD-060) — consumed, not changed
`Candidate`, `Location`, `Skill`, `FreeNow`, `RollingOff`, `NewJoiner`, `OpenRole`,
`CandidateSource`, `ProficiencyLevel` — all from `dsm/models.py`.

### Module-local — `dsm/ingest/models.py` (all `frozen=True`)
```python
class RowIssue(BaseModel, frozen=True):
    sheet: str            # tab name, e.g. "Beach"
    row_number: int       # 1-based spreadsheet row (matches what a human sees in Excel)
    email: str | None     # the row's email if it could be read, else None
    reason: str           # human-readable: "unparseable Join Date: '7th July'"

class IngestSummary(BaseModel, frozen=True):
    workbook_path: str
    candidate_rows_seen: int
    candidates_ingested: int
    role_rows_seen: int
    roles_ingested: int
    blank_rows_skipped: int
    duplicate_emails_skipped: int
    issues: list[RowIssue] = Field(default_factory=list)

class IngestError(Exception):          # structural failure (missing tab/column) — I-LOAD-2
    ...
```
> OQ-5 (signed off): **no `IngestResult` wrapper.** The documented `dict + list` phase
> contract is kept and the typed `IngestSummary` is returned as a third element.

## Public API
```python
def ingest_workbook(
    path: str | Path,
) -> tuple[dict[str, Candidate], list[OpenRole], IngestSummary]: ...
#         candidates keyed by email (AD-012; first-occurrence wins), roles, summary
```
Pure except for the single file read at the edge (`docs/tech.md`: side effects at the edges).
No network, no LLM, no clock, no randomness (I-DET-1).

## Internal structure (`sheets.py`)
Small, independently-testable, pure helpers + one orchestrating reader:

- `parse_date(cell) -> date` — accepts `date` / `datetime` / ISO `YYYY-MM-DD` `str`; raises
  `ValueError` otherwise (caught → `RowIssue`, I-EDGE-3).
- `parse_skills(raw: str | None) -> list[Skill]` — split on `,`, strip, lowercase, drop
  blanks, de-dupe preserving order; proficiency = `INTERMEDIATE` default (OQ-2).
- `parse_location(location_text, chennai_open) -> Location` — split *Location* on `/`;
  `remote_eligible = chennai_open=="Yes" OR any segment matches /remote/i`; `city` = first
  non-remote segment (or the canonical remote label if all segments are remote);
  `country="India"`. Shared by candidate and role mapping (role calls it with
  `chennai_open=None`, so remote-eligibility comes only from the location text). (I-CAND-4,
  I-ROLE-1.)
- `_header_index(ws) -> dict[str, int]` — read row 2, map normalised header → column index;
  raise `IngestError` if a required header for that sheet is missing (I-LOAD-2/3).
- `_is_blank(row) -> bool` — all cells `None`/`""` after strip (I-EDGE-2).
- `_row_to_candidate(values, headers, source) -> Candidate` — builds the row's `Candidate`;
  raises `ValueError`/`ValidationError` on bad data (caught by the reader → `RowIssue`).
- `_row_to_role(values, headers) -> OpenRole` — builds the `OpenRole`; `required_skills=[]`,
  `description = "Required Skills: <raw>\nNotes: <notes>"` (OQ-3).
- `ingest_workbook(path)` — loads once, iterates the 4 tabs in fixed order
  (Open Roles, then Beach → Rolling Off → New Joiners), applies blank/duplicate/validation
  rules, accumulates counts + `RowIssue`s, returns the `(candidates, roles, summary)` tuple.

### Availability mapping (I-CAND-2)
| Tab          | `CandidateSource` | `AvailabilityState`                          |
|--------------|-------------------|----------------------------------------------|
| Beach        | `beach`           | `FreeNow()`                                  |
| Rolling Off  | `rolling_off`     | `RollingOff(expected_date=…, confidence=…)`  |
| New Joiners  | `new_joiner`      | `NewJoiner(join_date=…)`                     |

### Duplicate-email handling (I-EDGE-1)
A single `seen: set[str]` across all three supply tabs, populated in fixed tab+row order.
First occurrence is kept in `candidates`; any later row with a seen email → skip, increment
`duplicate_emails_skipped`, append a `RowIssue(reason="duplicate email; kept first occurrence
from <sheet> row <n>")`.

## Phase(s) involved
`ingest` (the first phase). Output feeds `index` (embedding) and `match/gates` downstream —
this spec stops at producing the typed `(candidates, roles, summary)` tuple.

## Edge cases (and the requirement each maps to)
- Blank data row → skipped, counted, not an error (I-EDGE-2).
- Duplicate email within/across tabs → first wins, rest reported (I-EDGE-1).
- Unparseable / missing required date → `RowIssue`, row dropped (I-EDGE-3, I-VAL-1).
- Confidence outside `{high,medium,low}` → `RowIssue` (I-CAND-5).
- Missing email or name → `RowIssue` (I-VAL-1).
- Empty skills cell → allowed; `skills=[]` (not an error). Logged only if email also missing.
- `Remote (India)` / `Remote-India` location → `remote_eligible=True` (I-CAND-4).
- Numeric `#` / `Days on Beach` cells arrive as `float` from openpyxl → not used in the
  `Candidate` (days-on-beach utilisation is out of scope, AD-050); ignored safely.
- Missing tab or required header → `IngestError` (fatal, I-LOAD-2), distinct from row issues.

## Eval cases to add
Unit tests (mock-free — synthetic in-memory workbook via `openpyxl.Workbook()`; **no network
or LLM**, `docs/tech.md`). The real `data/demand-supply.xlsx` is used in one smoke test.

- **test_parsing.py**
  - `parse_date` accepts `date`, `datetime`, `"2026-06-22"`; rejects `"7th July"`, `None`,
    `42`.
  - `parse_skills("Java, Kotlin , java")` → `[java, kotlin]` (lowercase, trimmed, de-duped),
    each `INTERMEDIATE`; `parse_skills(None)` → `[]`.
  - `parse_location("Bengaluru / remote-India", None)` → city=Bengaluru, remote=True;
    `("Chennai", "No")` → city=Chennai, remote=False; `("Chennai", "Yes")` → remote=True;
    `("Remote (India)", "No")` → remote=True.
- **test_sheets.py** (synthetic workbooks)
  - Beach row → `FreeNow`, `source=beach`. Rolling Off row → `RollingOff` with confidence.
    New Joiner row → `NewJoiner` + `source=new_joiner` (asserts OQ-1 representation).
  - Open Roles row → `required_skills==[]`, raw skills present verbatim in `description`,
    `co_location_required` correct, `start_date` parsed (I-ROLE-1/2).
  - Duplicate email across Beach+New Joiners → one candidate (Beach kept),
    `duplicate_emails_skipped==1`, one `RowIssue` (I-EDGE-1).
  - Fully-blank row → `blank_rows_skipped==1`, no issue (I-EDGE-2).
  - Unparseable join date → row dropped, one `RowIssue`, others still ingested (I-VAL-1).
  - Bad confidence → `RowIssue` (I-CAND-5).
  - Missing required header → `IngestError` (I-LOAD-2).
  - Determinism: two calls on the same workbook produce equal `(candidates, roles, summary)`
    (I-DET-1).
- **smoke (real file):** `ingest_workbook("data/demand-supply.xlsx")` → **35 candidates**
  (10 Beach + 10 Rolling Off + 15 New Joiners), **8 roles**, `issues==[]`,
  `duplicate_emails_skipped==0`, `blank_rows_skipped==0`. (Counts asserted against the real
  snapshot so silent shape-drift in the data fails the harness.)

> Note: the real workbook is clean (verified during spec authoring — no blank rows, no
> duplicate emails, all dates ISO). The edge-case coverage therefore relies on synthetic
> fixtures, not the shipped data.

## Determinism & PII notes
- No clock/RNG; ordering is sheet-order then row-order (I-DET-1).
- Ingest produces records that *contain* PII (`name`, `email`) — that is correct; the PII
  boundary (AD-010/011) is enforced later at the embed/LLM edges, not at ingest. Ingest does
  not call any LLM or network, so the import contract "`dsm.ingest` must not import
  `modal`/`httpx`" (`pyproject.toml`) stays green.
