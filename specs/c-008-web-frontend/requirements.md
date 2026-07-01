# c-008 — Web frontend (thin JSON API + single static page)

## User story
As the staffing manager, I want a minimal web UI — not just a CLI — so I can paste a role in plain
English **or** upload the demand sheet, see exactly how the role was read, confirm it, and review a
ranked, explainable shortlist with each candidate's evidence, trade-offs, and original résumé, then
record my put-forward / set-aside decisions for audit.

## Scope & framing
A **thin** FastAPI JSON API wrapping the **existing** query spine (`run_match` / `_run_role`) plus a
**single self-contained static page** (HTML + inline CSS/JS, no SPA/build) that calls it. The API
adds **no** matching, scoring, or eligibility logic — it is a transport + render edge over the same
orchestration the CLI uses. Web UI was previously out of MVP scope (`docs/product.md`); this slice
moves that boundary under **AD-XXX** and adds the `fastapi`/`uvicorn` dependency under the same ADR.

The crucial constraints carry over unchanged:
- **Eligibility stays deterministic + LLM-free** (AD-002). The API never decides who qualifies.
- **PII boundary holds** (AD-101/102/107). De-anonymisation happens only at the output edge via the
  existing `render_identities`; the web layer does **not** import `dsm.pii` directly — it reuses the
  CLI composition-root builders that already own the boundary.
- **Frozen contract untouched** (AD-060). The API serialises existing `dsm/models.py` types and adds
  **web-local** request/response wrappers in `dsm/web/models.py` (the `RoleIntake` precedent) — no
  change to `dsm/models.py`, so **no `make contract-snapshot`**.

## Out of scope (this slice)
Authentication / multi-user sessions · CORS for cross-origin hosting (page is same-origin) ·
batch / multi-role matching (AD-050 single-role stands) · candidate-level negation · streaming ·
the feedback **learning** loop (decisions are **capture-only**, append-only, and never feed ranking
— that is a separate D-slice) · editing the parsed role field-by-field in the UI (the operator
re-types prose or fixes the CSV and re-submits; the confirm step is the gate, AD-110).

## Functional requirements (EARS)

### FR-1 — Serve the page
- **FR-1-AC-1:** WHEN a browser requests `GET /`, the system SHALL return the single static
  `index.html` (the matcher UI) with `text/html`.
- **FR-1-AC-2:** WHEN `GET /healthz` is requested, the system SHALL return `200` with a small JSON
  body, performing no LLM/Milvus/gold access.

### FR-2 — Natural-language intake: parse + echo (two-step, door 1)
- **FR-2-AC-1:** WHEN a client `POST`s prose to `/intake`, the system SHALL parse it via the
  **existing** intake predictor (single-shot, `temperature=0`, content-hash cached — AD-110/AD-066),
  assemble it into an `OpenRole` via the existing `assemble_role`, and return a **role echo**
  (title, resolved start date **and** its original phrase, hard/desired skills, location,
  Python-**derived** `co_location_required`, and `exclude_cities`) plus the synthesized
  `role_id = "NL-<hash[:8]>"`.
- **FR-2-AC-2:** WHEN assembly reports a missing required gate field (location / start), the system
  SHALL return a `needs_clarification` status naming the missing field(s) and SHALL **not** run any
  gate — exactly mirroring the CLI's one-bounded-round behaviour (AD-110/FR-4).
- **FR-2-AC-3:** The `/intake` response SHALL **never** contain `co_location_required` as a
  client-settable field — it is server-derived only and returned for display (AD-002/FR-8).
- **FR-2-AC-4:** WHEN the prose is empty, the system SHALL return `400` with a human-readable reason
  and SHALL NOT call the LLM.

### FR-3 — Natural-language match: confirm + run (door 1)
- **FR-3-AC-1:** WHEN a client `POST`s `{prose, clarifications?, confirm: true}` to `/match/query`,
  the system SHALL re-derive the same `RoleIntake` from the deterministic parse cache (a cache hit —
  **no second LLM parse**), apply any clarification answers in Python (city / ISO date, never an
  LLM), assemble the `OpenRole`, and run the **existing** `_run_role` with `clarify_predict=None`
  (NL echo path — exactly one LLM interpretation of the role, AD-110).
- **FR-3-AC-2:** WHEN clarifications are still insufficient after one round, the system SHALL return
  `422` naming the still-missing fields and SHALL NOT gate.
- **FR-3-AC-3:** `demand_as_of` on the NL door SHALL be the run-date (today), so the freshness guard
  yields only `ok` / `refuse` (AD-111); a `refuse` SHALL surface as `409` with the verdict message.

### FR-4 — CSV / demand-sheet door (door 2)
- **FR-4-AC-1:** WHEN a client `POST`s a demand CSV (multipart) to `/demand/parse`, the system SHALL
  parse it via the **existing** `parse_demand` and return the banner `demand_as_of`, the ordered
  roles (id, title, location summary, start date), and the human-readable skipped-row lines.
- **FR-4-AC-2:** WHEN a client `POST`s a demand CSV + `role_id` (multipart) to `/match/role`, the
  system SHALL select that role and run the **existing** `_run_role` with the **live** clarify
  predictor (CSV path), using the banner date as `demand_as_of`.
- **FR-4-AC-3:** WHEN the `role_id` is absent from the CSV, the system SHALL return `404`; WHEN the
  CSV is unparseable, the system SHALL return `400` — neither shall produce a partial match.

### FR-5 — Shortlist response (both doors)
- **FR-5-AC-1:** Both match endpoints SHALL return the existing `ShortlistResult` **or**
  `NoMatchResult`, **de-anonymised** via the existing `render_identities` (real name/email — the
  authorised-human output edge, AD-107), wrapped in a web-local view that **additionally** carries
  the stable pseudonymous `candidate_id` per candidate (for `/resume` + `/decisions` addressing).
- **FR-5-AC-2:** The response SHALL preserve every explainability field the CLI emits: sub-scores,
  `combined_score`, flags, evidence citations (with source), narrative, hard/desired coverage,
  exclusion log, near-misses, and closest-on-skills — nothing summarised away.
- **FR-5-AC-3:** A query-side excluded candidate ("not Chennai") SHALL appear in the exclusion log
  and SHALL NOT be surfaced as a near-miss (AD-112) — the API does not re-introduce them.

### FR-6 — Résumé PDF (authorised-human surface)
- **FR-6-AC-1:** WHEN a client requests `GET /resume/{candidate_id}`, the system SHALL resolve the
  candidate's résumé bronze blob from gold citations (`source == PROFILE_PDF`, `source_hash`),
  fetch the bytes from the blob store, and stream them as `application/pdf`.
- **FR-6-AC-2:** WHEN the candidate exists but has **no** résumé (supply-sheet-only — no
  `PROFILE_PDF` citation), the system SHALL return `404` with a "no résumé on file" reason — a
  normal, non-error state.
- **FR-6-AC-3:** The résumé bytes (real name/contact) SHALL be served **only** to the authorised
  human browser and SHALL **never** be passed to any LLM or embed endpoint (AD-107 trust level).
- **FR-6-AC-4:** `candidate_id` in the URL SHALL be the pseudonymous HMAC id (AD-067) — safe in a
  URL/log; the endpoint SHALL NOT accept a real email.

### FR-7 — Decision capture (append-only)
- **FR-7-AC-1:** WHEN a client `POST`s decisions (`{run_id, role_id, reviewer, decisions: [{
  candidate_id, action: forward|set_aside, reason?}]}`) to `/decisions`, the system SHALL append
  them to a gitignored, append-only log keyed by `candidate_id` and return a confirmation.
- **FR-7-AC-2:** Recorded decisions SHALL NOT alter the current or any future shortlist — capture
  only; no learning loop (deferred). This SHALL be stated in the UI.
- **FR-7-AC-3:** The decision record SHALL key candidates by `candidate_id` (pseudonym), not name.

### FR-8 — Errors are typed JSON, PII-safe
- **FR-8-AC-1:** All error responses SHALL be JSON `{detail: <message>}` with an appropriate status
  (`400` bad input, `404` not found, `409` freshness refuse, `422` clarification still needed,
  `500` unexpected) — never an HTML stack trace.
- **FR-8-AC-2:** No error message SHALL contain a candidate name/email or a vault path (PII-safe
  logging — `candidate_id` only, mirroring the CLI).

## Non-functional requirements
- **NF-1 — Reuse, don't fork:** the API SHALL call the existing `run_match` / `_run_role` /
  `render_identities` / builders / `parse_demand` / `assemble_role` — it SHALL NOT re-implement gate,
  score, rank, or PII logic. A **parity test** SHALL assert the API match path yields the same
  result as the CLI path for a fixture.
- **NF-2 — Import boundary:** `dsm/web/` is a composition root (peer of `dsm/cli/`). It MAY import
  `dsm.cli.commands`, `dsm.match`, `dsm.index`, `dsm.ingest` (for the gold/blob read), `dsm.config`,
  `dsm.models`. It SHALL NOT import `dsm.pii` **directly** or any provider (`modal`/`httpx` as a
  client) — the PII wiring stays in the CLI builders it reuses. (Enforced by construction + a
  unit/AST assertion; not an import-linter `forbidden` contract, which would false-positive on the
  legitimate transitive `dsm.web → dsm.cli → dsm.pii` chain.)
- **NF-3 — Determinism untouched:** the API changes no pipeline internals; the determinism invariant
  and eval cassettes are unaffected. API response formatting MAY be non-deterministic (field order)
  but the underlying assessments SHALL be reproducible.
- **NF-4 — Config over constants:** host, port, and any limits live in `config/default.yaml::web.*`,
  never inline (tech.md rule 6).
- **NF-5 — Harness green:** `make check` (format, lint, typecheck, unit, import-contracts, Tier-1
  eval) SHALL be green; new behaviour SHALL have offline (cassette/stub) tests; live paths
  `skipif` no keys.

## Product invariants referenced
AD-002 (gates LLM-free) · AD-060 (frozen contract) · AD-067 (candidate_id = HMAC) ·
AD-101/102/107 (PII boundary + output de-anonymisation) · AD-110/AD-111 (NL intake + freshness) ·
AD-112 (query-side negation) · AD-050 (single role per request).
