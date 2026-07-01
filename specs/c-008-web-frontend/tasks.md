# c-008 — Tasks (one task = one commit)

Ordered, atomic, each mapped to acceptance criteria. `make check` green before each commit.

- [ ] **T-000 — ADR + scope shift + deps gate.** Add **AD-XXX** (web API + FastAPI/uvicorn dep +
  module placement + capture-only decisions posture) to `docs/decision.md`; reword `docs/product.md`
  § Out of scope so the web-UI boundary moves explicitly; add the web server line to `docs/tech.md`
  § Stack; add `fastapi`, `uvicorn`, `python-multipart` to `pyproject.toml`. Use the `AD-XXX`
  placeholder (resolved at merge via `/handoff-index`). *(NF-4, scope; gates all code below.)*

- [ ] **T-001 — Config.** Add `web: {host, port}` to `config/default.yaml`. *(NF-4.)*

- [ ] **T-002 — Web DTOs.** `dsm/web/__init__.py` + `dsm/web/models.py` with the request/response
  models from design.md. *(FR-2/3/4/5/6/7 contracts; golden rule 4 typed boundaries.)*

- [ ] **T-003 — Service composition.** `dsm/web/service.py`: `intake_echo`, `apply_clarifications`,
  `match_query`, `match_role`, `run_role_api` (freshness pre-guard + `_run_role` + `render_identities`
  + `build_view`), `resume_pdf`, `record_decisions`, and the typed service exceptions. Reuse
  `dsm.cli.commands` builders + `run_match`/`render_identities`/`parse_demand`/`assemble_role`.
  *(FR-2..7, NF-1, NF-2.)*

- [ ] **T-004 — FastAPI app.** `dsm/web/app.py`: routes, exception handlers (FR-8 status mapping),
  static mount for `/`. *(FR-1, FR-8.)*

- [ ] **T-005 — Static page.** `dsm/web/static/index.html`: the approved mockup CSS verbatim; JS
  rewritten to call the endpoints; drawer "Open original résumé (PDF)" → `/resume/{candidate_id}`;
  decisions → `/decisions`. *(FR-1..7 UI.)*

- [ ] **T-006 — `dsm serve`.** Add the `serve` command to `dsm/cli/main.py` (uvicorn over
  `web.host`/`web.port`). *(FR-1.)*

- [ ] **T-007 — Tests.** `tests/web/test_app.py` (TestClient + monkeypatched builders, all FR ACs),
  `tests/web/test_parity.py` (NF-1), `tests/web/test_imports.py` (NF-2). *(All ACs; NF-5.)*

- [ ] **T-008 — Harness + handoff.** `make check` green; adversarial review pass; update
  `docs/progress.C.md` via the lane handoff. *(Definition of Done.)*

## Done when
All FR acceptance criteria met · `dsm serve` boots the page · both doors return an explainable
shortlist with résumé + decision capture · PII boundary intact (no `dsm.pii` import in `dsm/web`,
real identity only at the output edge, résumé never to an LLM) · `dsm/models.py` unchanged · all
tests + import contracts + Tier-1 invariants green.
