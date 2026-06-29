# C-006 Natural-Language Query Intake — Tasks

> **Lane:** C · **Slice:** C-006
> One task = one commit, imperative, referencing this spec. `make check` GREEN after each.
> Branch: `feat/c/006-nl-query-intake`. Incorporates the pre-sign-off adversarial review (FR-8
> co-location, NL freshness ok/refuse, NL skips clarify's LLM, temperature pinning).

---

## T-000 · ADR sign-off gate

Record in `docs/decision.md` (use `AD-XXX`/`AD-XXY` placeholders on the branch; `/handoff-index`
assigns real numbers at merge — footer says next is AD-110):

- **AD-XXX** — Natural-language intake front door (single-shot bare `dspy.Predict` temp-0 →
  existing `OpenRole`; **`co_location_required` Python-derived, never LLM** (FR-8); LLM-parsed
  `location_city`/`start_date` gated by human confirmation; NL **replaces** clarify's LLM with the
  echo path (one interpretation call); confirmation echo + one bounded clarification round;
  `demand_as_of = run-date`; synthesized `role_id`; `--yes` = explicit human pre-confirmation;
  determinism via temp-0 + content-hash cache + pinned derivation version; **no frozen-contract change**).
- **AD-XXY** — Relative-date resolution (LLM resolves with injected today; Python validates:
  calendar + `[today, today+max_horizon]` sanity bound) **+ NL freshness semantics** (`ok`/`refuse`
  only; `warn` structurally unreachable; NL stricter than a backdated CSV — accepted).

**Neither touches `dsm/models.py`** → no `make contract-snapshot`. Confirm scope fences hold
(A2 `exclude_*` and A4 relaxation remain deferred to their own ADRs).

**STOP for human sign-off on the full spec before any code (Golden rule 1).** Resolve the three
open questions (sequence number; `--yes` vs piped-stdin; LLM-resolves-date vs deferred Python resolver).

**Acceptance:** AD-XXX/AD-XXY recorded with correct cross-refs to this spec, the architecture doc,
and AD-002/060/064/066/087/101; footer integrity intact (`tests/docs`).

---

## T-001 · Config + prompt + `RoleIntake` type (inert scaffolding)

- `config/default.yaml`: add
  ```yaml
  nl_intake:
    prompt_version: "intake-v1"   # BUMP whenever config/prompts/role_intake.md changes (AD-066) — gates the parse cache
    temperature: 0                # pinned; the LLM resolves dates/skills deterministically (temp 0)
    max_horizon_days: 730         # sanity bound (NOT a gate parameter): reject a resolved start date > ~2y out
    cache_dir: "data/.cache/nl_intake"   # content-addressed parse cache; under the already-gitignored data/.cache/*
  ```
  Reuse `models.reasoning_llm` (do not duplicate the model id).
- `config/prompts/role_intake.md`: the intake instruction. MUST contain the verbatim directive
  *"leave any field absent from the text as null — never guess."* Cover: classify skills into the
  `hard_skills` / `desired_skills` buckets from phrasing ("must"/proficiency words → hard;
  "nice to have"/"ideally" → desired); normalise skill names lowercase; map an explicit proficiency
  word to `min_proficiency` else leave it null; resolve a relative start date to ISO using the
  injected `today`; emit the original date phrase; **extract** what the request says about
  location/date but **never decide eligibility and never emit a co-location flag** (co-location is
  derived by code; gates are deterministic); the text describes the ROLE, not a person.
- `dsm/match/models.py`: add `RoleIntake` (§3.1) — **no `co_location_required` field**. Reuse
  `SkillRequirement` from `dsm/models.py`.
- Confirm `data/.cache/*` is already gitignored (this step should be a **no-op**; do not add a
  redundant rule).

**Tests:** `RoleIntake` instantiates with all-default (null) fields and has no `co_location_required`
attribute; the prompt file loads and contains the "never guess" directive (FR-1-AC-3).

**Acceptance:** `make check` GREEN. No behaviour wired yet.

---

## T-002 · Intake core (`dsm/match/intake.py`) — signature, predictor, assembly, validation, cache key

Implement (§3.2–3.4, §4 `assemble_role`):
- `RoleIntakeSignature` + `IntakePredictor` seam + `make_intake_predictor(lm)` (bare single-shot
  `dspy.Predict`, **no baked demos**; `today` injected; `dspy.context(lm=lm)`).
- `assemble_role(intake, today, *, max_horizon_days, role_id) -> OpenRole | ClarificationNeeded`:
  start-date validation (calendar + plausibility window), location assembly (incl. remote),
  **Python-derived `co_location_required`** (FR-8), **forced skill depth per bucket** (FR-1-AC-5),
  `preferred_skills`/`onsite_cities` left default. `ClarificationNeeded` typed result.
- `intake_cache_key(...)` (pure sha256 over normalised prose | today | model_id | prompt_version)
  + `IntakeCache` protocol + `NullIntakeCache`.
- Module imports `dspy` + `dsm.config` + `dsm.models` + `dsm.match.models` + `structlog` + stdlib
  **only** (NF-5) — no `dsm.pii`/`dsm.ingest`/`modal`/`httpx`.

**Tests** (`tests/match/test_intake.py`, fake predictor — no network): the §8 phrasing fixtures →
correct `OpenRole`; the relative-date case (FR-2-AC-1); malformed/out-of-window ISO → `start`
missing (FR-2-AC-2/3); missing-location → `ClarificationNeeded(["location"])` (FR-4); **co-location
derived not LLM-set** + named-city→True / remote→False (FR-8); **forced depth** overrides a
mis-bucketed element (FR-1-AC-5); never-guess (FR-1-AC-4); cache-key stability (FR-6-AC-2/3);
bare-`dspy.Predict`-no-demos (NF-6).

**Acceptance:** `make check` GREEN; import contracts unchanged (verify `dsm.match.intake` adds no
forbidden import).

---

## T-003 · CLI wiring (`dsm/cli/commands.py`) — `--query` front door

- Refactor: extract `_run_role(role, demand_as_of, *, clarify_predict, gold_dir, db_path,
  vault_path)` from `_match_role`; rewire `_match_role` (CSV) to call it with
  `clarify_predict=_build_clarify_predictor(config)`. (Pure refactor — `dsm match --role-id`
  behaviour unchanged; existing CLI tests stay green.)
- Add `_build_intake_predictor(config)` over `PseudonymisedLM(..., temperature=0)` (pass-through,
  no `pii_context`; temperature pinned explicitly — FR-1-AC-2), monkeypatched in tests.
- Add `FileIntakeCache` (JSON under `nl_intake.cache_dir`; corrupt/unreadable → miss; never crash).
- Add `_match_query(prose, gold_dir, db_path, vault_path, *, yes)`: cache→predict→assemble→
  clarify-round(once, pure Python, no LLM)→echo(always)→confirm→`_run_role(role, today,
  clarify_predict=None, …)` (§4). Echo lists parser-populated gating-relevant fields incl. the
  Python-derived `co_location_required` (FR-3-AC-1).
- Extend `match(...)`: add `--query` + `--yes`; make `--role-id` `Optional[str]=None`; require
  exactly one of `--query`/`--role-id` (FR-7-AC-4). `explain` unchanged (`--role-id` only). NL path
  renders identities + prints JSON exactly as the CSV path.

**Tests** (`tests/cli/test_match_query.py`): reuse the **full** `wired` seam set (clarify, score,
embed, query-store, near-miss) **plus** a fake `_build_intake_predictor` + fake `IntakeCache`
(nothing live offline, NF-1). Cover: happy path + echo + `role_id` `NL-…` + `demand_as_of=today`
(FR-3/FR-7); missing-location clarification via `input="Chennai\ny\n"` with predictor-called-once
(FR-4); decline-at-confirm aborts (FR-3-AC-2); cache-hit predictor-once (FR-6-AC-1); both-flags /
neither-flag error (FR-7-AC-4); NL freshness `refuse` exit + no `warn` flag on the NL path (FR-7-AC-2).

**Acceptance:** `make check` GREEN; all existing CLI/orchestrator tests still pass (refactor is
behaviour-preserving for the CSV door).

---

## T-004 · Docs + lane refresh (same PR — no drift)

- `README.md`: add `dsm match --query "<prose>"` usage (echo + confirm flow; `--yes`). **Cite
  config keys** (`config/default.yaml::nl_intake.{prompt_version,max_horizon_days}`) — do **not**
  restate `730` / `"intake-v1"` as literals (doc-hygiene; README is a STEERING doc).
- `ee-query-architecture.md`: add an "amendment since sign-off" note — the prose intake pre-step is
  a parallel front door to §6.1 producing the same `OpenRole`, and on the NL path intake replaces
  the §6.2 clarify LLM pass (Status-header policy).
- `docs/progress.C.md`: update **In flight** → **Session log** (newest first) + **Next up** via
  `/handoff` (lane from `.claude/lane`). Do **not** edit `docs/progress.md` (index) — that happens
  at merge via `/handoff-index`.
- `docs/backlog.md`: note the deferred fast-follows (fully-deterministic Python date resolver; A2
  negation; A4 relaxation; offline MIPROv2 compile; NL `explain`; the free-form-`--query`
  unscanned-prose risk from FR-5-AC-2).

**Acceptance:** `make check` GREEN (incl. `tests/docs`); `make decisions-status` runs clean (the
AD-XXX/AD-XXY placeholders are unlisted until `/handoff-index` assigns real numbers at merge —
they are not digit-ADRs); spec acceptance criteria all met.

---

## T-005 · NL-intake parse-quality eval (`make eval`)

Add the parse-accuracy + live-quality layer (design §8 "NL-intake parse-quality eval"):
- `tests/fixtures/nl_intake_golden.json` — signed-off golden phrasings: prose + `recorded_intake`
  (the golden parse, a `RoleIntake`) + `expected` (assembled `OpenRole` / `ClarificationNeeded`).
- `dsm/eval/nl_intake_golden.py` — typed loader (`NLIntakeGolden`/`NLIntakeCase`, mirrors
  `golden_set.py`; `review_status` sign-off gate; reuses the frozen `RoleIntake`).
- `tests/eval/test_nl_intake.py` — **`eval_offline`** deterministic replay (golden parse →
  `assemble_role` → expected) + **`eval_live`** key-gated real-LLM smoke (structural fields;
  start-date validity/window only). Mirrors `test_signatures` + `test_live_smoke`.

**Acceptance:** `make eval` GREEN — offline tier deterministic (7 tests), live tier passes with keys
(3 cases) and `skipif` without. Not in `make check` (eval-only, like the AI eval layer). `make
check` still GREEN (lint/typecheck cover the new module + test).

---

## Task → acceptance-criterion map

| Task | Covers |
|------|--------|
| T-000 | ADR gate (NF-2 no contract change; AD-002 co-location reasoning) |
| T-001 | FR-1-AC-3, FR-8-AC-1, NF-3 |
| T-002 | FR-1 (AC-1/4/5), FR-2 (AC-1/2/3), FR-4 (AC-1 logic), FR-6 (AC-2/3), FR-8 (AC-1/2), NF-4/5/6 |
| T-003 | FR-1-AC-1/2, FR-3 (all), FR-4 (all), FR-5 (all), FR-6-AC-1/4, FR-7 (all), FR-8-AC-3, NF-1 |
| T-004 | DoD docs/lane; backlog fast-follows |
| T-005 | Parse-quality eval (in-scope; `make eval` offline + live tiers) |
