# c-008 — Design

## Modules touched / added
| Path | Change |
|---|---|
| `dsm/web/__init__.py` | **new** — package marker. |
| `dsm/web/models.py` | **new** — web-local request/response DTOs (Pydantic; not frozen `dsm.models`). |
| `dsm/web/service.py` | **new** — composition root logic: intake, run-role, view-building, résumé lookup, decision log. Reuses `dsm.cli.commands` builders + `run_match`/`render_identities`. |
| `dsm/web/app.py` | **new** — FastAPI app: routes, exception handlers, static mount. |
| `dsm/web/static/index.html` | **new** — single self-contained page (the mockup, wired to `fetch`). |
| `dsm/cli/main.py` | **edit** — add `dsm serve` command (uvicorn). |
| `config/default.yaml` | **edit** — add `web:` section (host, port). |
| `pyproject.toml` | **edit** — add `fastapi`, `uvicorn`; `python-multipart` (file upload); `httpx` already present (TestClient). |
| `docs/{product,tech,decision}.md` | **edit** — scope shift + Stack line + ADR (AD-XXX). |
| `tests/web/` | **new** — TestClient tests + a CLI/API parity test. |

No change to `dsm/models.py` → no `make contract-snapshot`. No change to `dsm/match`/`dsm/index`
(the spine is reused as-is).

## Why reuse `_run_role` directly (not fork the composition)
`_run_role(role, demand_as_of, *, clarify_predict, gold_dir, db_path, vault_path) -> (result, vault)`
already performs hydrate → freshness → clarify → `run_match`, wiring the PII-aware score predictor
and vault. The only UI coupling is its **freshness-REFUSE branch** (`typer.echo` + `typer.Exit(1)`).
The web service **pre-computes** the freshness verdict itself (via the same `_freshness_for`) to
return a clean `409` with the message, then calls `_run_role` on the non-refuse path (so its REFUSE
branch is never reached). This reuses the entire PII-safe composition with **zero duplication** and
keeps the CLI tests' monkeypatch pattern working (the web service calls the builders **through** the
`commands` module, so `monkeypatch.setattr(commands, "_build_*", ...)` covers both doors).

## Data contracts (`dsm/web/models.py`)
All Pydantic v2, web-local (the `RoleIntake`/`ScorecardClarification` precedent — module-local DSPy/
API types, never in frozen `dsm.models`).

```python
# ── intake / NL door ──
class IntakeRequest(BaseModel):          # POST /intake, POST /match/query body
    prose: str
    clarifications: Clarifications | None = None
    confirm: bool = False                # /match/query requires True

class Clarifications(BaseModel):
    location: str | None = None          # city, or "remote"
    start: str | None = None             # ISO date

class RoleEcho(BaseModel):               # how the role was read (display only)
    role_id: str
    title: str
    location: str                        # "Chennai" | "remote (India)" | "any (distributed)"
    co_location_required: bool           # SERVER-DERIVED, display only
    exclude_cities: list[str]
    start_date: str                      # ISO
    start_phrase: str | None
    hard_skills: list[str]
    desired_skills: list[str]
    notes: str | None

class IntakeResponse(BaseModel):
    status: Literal["ready", "needs_clarification"]
    role_id: str
    echo: RoleEcho | None                # present when ready
    missing: list[str] = []              # present when needs_clarification

# ── CSV door ──
class RoleSummary(BaseModel):
    role_id: str; title: str; location: str; start_date: str; co_location_required: bool
class DemandParseResponse(BaseModel):
    demand_as_of: str; roles: list[RoleSummary]; skipped: list[str]

# ── shortlist view (both doors) ──
class CandidateView(BaseModel):
    candidate_id: str                    # pseudonym (for /resume + /decisions); from pre-render result
    name: str; email: str                # de-anonymised (post render_identities)
    source: str; location: str; availability: str   # grade isn't on the serving Candidate (AD-091)
    years_experience: int | None
    has_resume: bool                     # PROFILE_PDF citation present in gold
class AssessmentView(BaseModel):
    candidate: CandidateView
    skill_match_score: float; feedback_score: float; combined_score: float
    hard_skill_coverage: float; desired_skill_coverage: float
    flags: list[FlagView]; evidence: list[EvidenceView]; narrative: str
class NearMissView(BaseModel):
    candidate_id: str; name: str; reason: str; gap_summary: str; selection_rationale: str | None
class ExclusionView(BaseModel):
    candidate_id: str; display: str; reason: str; detail: str   # display = de-anon email (no name)
class MatchResponse(BaseModel):
    role_id: str
    run_id: str                          # synthesized per match (for /decisions keying)
    outcome: Literal["shortlist", "no_match"]
    shortlist: list[AssessmentView] = []
    no_match_reason: str | None = None
    near_misses: list[NearMissView] = []
    closest_on_skills: list[NearMissView] = []
    exclusions: list[ExclusionView] = []
    total_eligible: int | None = None
    config_snapshot: dict | None = None

# ── decisions ──
class DecisionItem(BaseModel):
    candidate_id: str; action: Literal["forward", "set_aside"]; reason: str | None = None
class DecisionRequest(BaseModel):
    run_id: str; role_id: str; reviewer: str; decisions: list[DecisionItem]
class DecisionResponse(BaseModel):
    recorded: int; run_id: str
```

### Building the view (the candidate_id ↔ identity pairing)
`render_identities` rewrites identity fields **in order** (per-item `model_copy`), so the
pseudonymised result and the rendered result are structurally parallel. The service zips them:
`candidate_id` comes from the **pre-render** result (`assessment.candidate.email == candidate_id`),
`name`/`email` from the **post-render** result. This gives the browser a stable pseudonymous handle
for `/resume` + `/decisions` while showing real identity — and keeps `dsm/models.py` frozen.
`has_resume` is computed by the service from gold citations.

## Endpoints (`dsm/web/app.py`)
| Method | Path | Body | Returns | Notes |
|---|---|---|---|---|
| GET | `/` | — | `index.html` | static |
| GET | `/healthz` | — | `{status:"ok"}` | no I/O |
| POST | `/intake` | `IntakeRequest` (prose) | `IntakeResponse` | parse+echo; no gate |
| POST | `/match/query` | `IntakeRequest` (confirm) | `MatchResponse` | NL door → `_run_role(clarify=None)` |
| POST | `/demand/parse` | multipart CSV | `DemandParseResponse` | role picker |
| POST | `/match/role` | multipart CSV + `role_id` | `MatchResponse` | CSV door → `_run_role(clarify=live)` |
| GET | `/resume/{candidate_id}` | — | `application/pdf` \| 404 | authorised-human surface |
| POST | `/decisions` | `DecisionRequest` | `DecisionResponse` | append-only |

`StaticFiles`/`FileResponse` serves the page; the app reads `web.host`/`web.port` from config only
for `dsm serve` (uvicorn).

## Service functions (`dsm/web/service.py`)
- `intake_echo(prose, *, gold_dir, config) -> IntakeResponse` — cache-key → predictor (via
  `commands._build_intake_predictor`/`_build_intake_cache`) → `assemble_role`; build `RoleEcho` or
  `needs_clarification`.
- `apply_clarifications(partial: RoleIntake, c: Clarifications) -> RoleIntake` — pure mirror of the
  CLI's `_clarify_missing` (city / "remote" / ISO date), no LLM.
- `match_query(req, *, gold_dir, db_path, vault_path) -> MatchResponse` — re-parse (cache hit) +
  clarifications → `assemble_role` → (still missing → `ClarificationRequired`) → `run_role_api`.
- `match_role(csv_bytes, role_id, *, gold_dir, db_path, vault_path) -> MatchResponse` — `parse_demand`
  (from a temp file) → select role → `run_role_api` with live clarify.
- `run_role_api(role, demand_as_of, *, clarify_predict, gold_dir, db_path, vault_path) -> MatchResponse`
  — **freshness pre-guard** (`commands._freshness_for`; REFUSE → `FreshnessRefused`) → `_run_role`
  → `render_identities` → `build_view(pseudo, rendered, gold_dir)`.
- `resume_pdf(candidate_id, *, gold_dir, bronze_dir) -> bytes` — resolve the bronze blob hash, then
  `LocalFSBlobStore(bronze_dir).get(hash)`; raise `ResumeNotFound` (→404) when the candidate has no
  résumé, and map a missing-blob `OSError` to a clean 404 too. **Amendment since sign-off:** the
  blob hash is resolved **primarily from silver** — the `NormalizedRecord` with
  `source_type == RESUME` carries the bronze `source_hash` (`silver_dir = gold_dir.parent/"silver"`).
  Gold `profile_pdf` citations are a *fallback* only — in practice the current enrich/merge leaves
  their `source_hash` empty, so silver is the reliable link (verified against real ingested data).
- `record_decisions(req, *, decisions_dir) -> DecisionResponse` — append one JSON line per decision
  to `decisions/<run_id>.jsonl` (gitignored), `candidate_id`-keyed.

Typed service exceptions (`dsm/web/service.py`): `FreshnessRefused`, `RoleNotFound`,
`ClarificationRequired(missing)`, `ResumeNotFound`, `BadDemandCSV`, `EmptyQuery` — the app's
exception handlers map each to its FR-8 status. Paths default to the same `data/` roots the CLI uses
(`_GOLD_DEFAULT`, bronze, identity), overridable for tests.

## Static page (`dsm/web/static/index.html`)
The approved mockup, CSS verbatim (warm-paper + cobalt, tri-voice type, profile drawer). The mocked
JS data is replaced by `fetch` calls:
- NL "Read it →" → `POST /intake` → fill confirm card from `echo`; `needs_clarification` → reveal
  inline city/date inputs.
- "Find matches" → `POST /match/query {prose, clarifications, confirm:true}` → render `MatchResponse`.
- CSV upload → `POST /demand/parse` → render the role picker; pick + "Find matches" →
  `POST /match/role` (re-send the held `File` + `role_id`) → render.
- Name click (shortlist or ruled-out) → open the drawer from the already-loaded view; **Open original
  résumé (PDF)** → `window.open('/resume/'+candidate_id)`; `has_resume:false` → "no résumé on file".
- "Save decisions" → `POST /decisions`.
- A `no_match` outcome renders the reason + near-misses + closest-on-skills.

## Edge cases
- **Empty prose** → `EmptyQuery` → 400 (no LLM).
- **Clarification needed** → `/intake` returns `needs_clarification`; `/match/query` without enough
  answers → 422.
- **Freshness refuse** (NL or backdated CSV) → 409 with verdict message.
- **Role id not in CSV** → 404; **unparseable CSV** → 400.
- **Supply-only candidate** (no résumé) → `/resume` 404 + `has_resume:false` in the view (drawer
  shows "no résumé on file").
- **Vault miss** → `render_identities` keeps `candidate_id` + warns (AD-107); the view's `name`/
  `email` then equal the `candidate_id` — surfaced, never crashed.
- **No gold / empty pool** → `NoMatchResult` rendered normally.

## Eval / tests to add (`tests/web/`)
Pattern mirrors `tests/cli/test_orchestrator.py`: tmp gold via `write_gold`, tmp CSV, vault seeded
via `FileVault`, **monkeypatch `commands._build_*`** to stubs (no LLM/Modal/Milvus), FastAPI
`TestClient`.
- `test_app.py`:
  - `GET /healthz` → 200; `GET /` → HTML.
  - `POST /match/role` (tmp CSV + gold) → `MatchResponse` with shortlist; `candidate_id` present;
    de-anonymised name when vault seeded; `cid:` pseudonym absent from the rendered identity.
  - `POST /intake` ready vs `needs_clarification` (missing start) — stubbed intake predictor.
  - `POST /match/query` end-to-end with stubbed intake predictor → shortlist; missing-after-clarify
    → 422.
  - Freshness refuse (stale gold) → 409.
  - `role_id` not found → 404; unparseable CSV → 400.
  - `GET /resume/{cid}` → 200 `application/pdf` for a gold with a PROFILE_PDF citation + seeded blob;
    404 for a supply-only gold.
  - `POST /decisions` → appends JSONL, keyed by `candidate_id`; capture-only.
  - **PII:** an error response and the decisions log contain no name/email.
- `test_parity.py` (NF-1): the `/match/role` view and a direct CLI `_match_role` run produce the
  same ranked candidate ids + scores for one fixture.
- `test_imports.py` (NF-2): assert `dsm.web.service`/`dsm.web.app` source does not directly
  `import dsm.pii` (AST scan) and does not import `modal`/`httpx` as a provider.

All `eval_offline`-class (deterministic, stubbed). A live smoke is unnecessary — the spine's live
behaviour is already covered by the CLI/eval tiers; the API adds no LLM logic.
