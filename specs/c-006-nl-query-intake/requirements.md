# C-006 Natural-Language Query Intake — Requirements

> **Lane:** C (Quality, PII & Interface) · **Slice:** C-006 (NL intake front door)
> **Source:** `workshop-feedback/iteration-2-gaps-and-interview-questions.md` § A1 (NL query input) + § A3 (relative-date parsing). Iteration-2 backlog item #1 (P0/P1).
> **Architecture ref:** `ee-query-architecture.md` §6.1 (demand parse) + §6.2 (clarify) — this slice adds a *parallel* prose front door that produces the **same** typed `OpenRole` the CSV parser produces. On the NL path the intake LLM **is** the free-text interpretation step (it replaces `clarify`'s LLM pass — see FR-7).
> **Prerequisite:** b-002 query pipeline (merged) — `clarify_role`, `run_match`, `render_identities`, the `PseudonymisedLM` boundary (AD-101), and `parse_demand` (the CSV front door this mirrors).
> **Sequence note:** C-006 (not C-005). `c-005` stays reserved for the AD-084 outbound NER/org scan in `docs/backlog.md`; this slice was pulled forward by explicit request and does not disturb that reservation. Renumber at sign-off if the team prefers.
>
> **This revision folds in a pre-sign-off adversarial review** (5 lenses; 1 confirmed blocker, 1 major, plus refinements) — see § Review fixes applied.

---

## User story

As a **staffing manager**, I want to type an open role in prose — *"senior Kotlin engineer
in Chennai, payments, starting next month"* — and have the system parse it into the same
typed `OpenRole` the demand CSV produces, **show me what it understood**, and only then run
the unchanged matching pipeline — so I can ask for a shortlist without hand-editing a CSV,
while the deterministic, auditable spine below the role stays unchanged.

This is the **literal entry point to usability** (the workshop's flagship miss). It is
**low-risk by construction**: the parser only *produces* an `OpenRole`; gates, scoring, and
ranking are untouched (AD-002, Golden rules 2–3).

---

## Scope

### In scope
- A free-text → `OpenRole` parser as a **new pre-step**, exposed as `dsm match --query "<prose>"`
  (a parallel front door to the existing `--role-id` CSV path).
- A **single-shot** class-based DSPy `Signature` with a Pydantic output, `dspy.Predict` at
  `temperature = 0`. Not a chat loop, not ReAct, not `dspy.Refine`.
- **Relative-date resolution** ("next month", "in 3 weeks"): inject today's date into the
  signature; the LLM resolves to a concrete ISO date; **Python validates it deterministically**
  before it reaches the availability gate.
- A **confirmation echo**: print the parsed `OpenRole`; require confirmation before gating.
- A **single bounded clarification round** for a missing required gate field (location / start):
  Python inspects the typed result and asks once — the LLM does not loop.
- **Determinism**: same prose + same run-date + same `(model_id, prompt_version)` → same
  `OpenRole`. Pinned derivation version (AD-066) + a content-hash parse cache.
- `--role-id` becomes **optional**; `match` requires **exactly one** of `--query` / `--role-id`.
- **Parse-quality eval** (`make eval`): a signed-off golden set of real phrasings → expected typed
  `OpenRole`, with an offline (deterministic, golden-parse replay) tier and a key-gated live tier
  (real LLM). Mirrors the existing Tier-2/Tier-3 harness. *(Brought in-scope from the original
  fast-follow note.)*

### Out of scope (do not touch)
- **A2 — query-side negation / `exclude_*` fields.** Mutates the frozen `OpenRole` contract
  (AD-060); needs its own ADR + `make contract-snapshot`. Deferred.
- **A4 — constraint-relaxation retries.** Deferred; needs its own ADR (would risk the
  non-agentic invariant).
- Slack / email / web front doors (`docs/product.md` § Out of scope) — CLI only.
- Multi-role / batch prose. One role per invocation (AD-050).
- **NL via `dsm explain`.** `explain` stays `--role-id` only this slice; NL `explain` is a
  fast-follow.

---

## Functional requirements (EARS format)

### FR-1 · Parse prose → typed intake via a single-shot DSPy signature

**When** the system receives a free-text role request via `dsm match --query`,
**the system shall** run a single `dspy.Predict` over a class-based `RoleIntakeSignature`
(at `temperature = 0`, instructions from `config/prompts/role_intake.md`) that takes the
request text **and** today's date and emits a typed `RoleIntake` Pydantic object — never a
chat loop, ReAct agent, or `dspy.Refine`.

**Where** a field is absent from the prose,
**the system shall** leave that field `null`/empty in `RoleIntake` — the signature
instruction explicitly forbids guessing ("leave any field absent from the text as null —
never guess").

| AC | Criterion |
|----|-----------|
| FR-1-AC-1 | `RoleIntakeSignature` is a `dspy.Signature` subclass with input fields `request_text` + `today` and a single `RoleIntake` output field; the predictor is a **bare** `dspy.Predict` with instructions loaded from config and **no baked-in demos** (not `Refine`/`ReAct`/`ChainOfThought`-with-retries) — so it can later be compiled offline (NF-6). |
| FR-1-AC-2 | The live intake predictor pins `temperature = 0` **explicitly** on the LM (the repo's existing clarify/score builders do *not* pin it — they rely on the dspy default; intake must not). Verified by a builder unit test that observes the temperature reached a fake LM. |
| FR-1-AC-3 | `config/prompts/role_intake.md` contains the verbatim directive *"leave any field absent from the text as null — never guess."* |
| FR-1-AC-4 | A prose request naming no location leaves `RoleIntake.location_city = None` and `remote_within_country = False` (not a hallucinated city). |
| FR-1-AC-5 | Skill names are normalised to lowercase during assembly. `RoleIntake` carries **two skill lists** (`hard_skills`, `desired_skills`); `assemble_role` **forces** `SkillDepth` from the bucket (every `hard_skills` element → HARD, every `desired_skills` element → DESIRED) so the per-element depth can never contradict the bucket. `min_proficiency` is best-effort: the prompt maps an explicit proficiency word ("expert"/"advanced") to a `ProficiencyLevel`, else leaves it `None` (the echo shows it; no proficiency is fabricated). |

### FR-2 · Resolve relative dates against an injected run-date, then validate deterministically

**When** the prose contains a relative start date,
**the system shall** inject today's ISO date into the signature so the LLM resolves the
phrase to a concrete ISO date (`start_date_iso`), and **the system shall** preserve the
original phrase (`start_date_phrase`) for the echo and logs.

**When** the system receives `start_date_iso` from the LLM,
**the system shall** validate it in pure Python *before* it reaches the availability gate:
it must parse as a valid calendar date (`date.fromisoformat`) **and** fall within
`[today, today + config nl_intake.max_horizon_days]` (a **sanity bound, not a gate
parameter** — see § design).

**Where** `start_date_iso` is absent, malformed, or out of the plausibility window,
**the system shall** treat `start` as a missing/invalid required field (→ FR-4), never
silently pass a bad date to the gate.

| AC | Criterion |
|----|-----------|
| FR-2-AC-1 | With `today = 2026-06-29`, a `RoleIntake` carrying `start_date_iso = "2026-07-29"` assembles an `OpenRole` with `start_date = date(2026, 7, 29)` and logs the resolved date. *(The validation guarantees calendar-validity + plausibility — a guard. The resolved date's **value-determinism** comes from `temperature = 0` + the content-hash cache + derivation-version pinning, not from this validation — see FR-6 and open-question 3.)* |
| FR-2-AC-2 | A malformed `start_date_iso` (e.g. `"2026-13-40"` or `"next month"`) is rejected by Python validation and surfaces `start` as needing clarification — it never reaches the gate. |
| FR-2-AC-3 | An ISO date before `today` or beyond `today + nl_intake.max_horizon_days` is rejected (plausibility guard) and surfaces `start` as needing clarification. |
| FR-2-AC-4 | The availability gate (`dsm/match/gates.py`) is unchanged and receives a validated `date` — no LLM value reaches it unvalidated. |

### FR-3 · Confirmation echo before gating

**When** the system has assembled a valid `OpenRole` from prose,
**the system shall** print a human-readable echo of the parsed role (title, location +
remote/co-location, resolved start date with its original phrase, hard skills, desired
skills, notes) and **require confirmation** before any gate runs.

**Where** the operator declines,
**the system shall** abort without running the pipeline (no shortlist produced).

| AC | Criterion |
|----|-----------|
| FR-3-AC-1 | The echo lists every **parser-populated, gating-relevant** field, including the **Python-derived** `co_location_required` (FR-8) and the resolved `start_date`, so the human can catch a misparse before it gates. Fields the NL parser never populates (`onsite_cities`, `preferred_skills`) are shown as `(none — not parsed from prose)` or omitted — the echo never implies they were extracted. |
| FR-3-AC-2 | Declining at the confirmation prompt aborts the run; no `ShortlistResult`/`NoMatchResult` is printed. |
| FR-3-AC-3 | Confirming proceeds to the **unchanged** spine (freshness → clarify-echo → gate → exact filter → recall → rerank → score → rank). |
| FR-3-AC-4 | `--yes` pre-confirms for non-interactive/scripted use. It is an **explicit human override** (the operator asserts they have reviewed the request), **not** an LLM decision: the parsed role is **always echoed** (even under `--yes`) for audit, gates stay pure Python, and `co_location_required` is Python-derived regardless (FR-8). The default is interactive confirmation; tests exercise the interactive path as primary. *(See open-question 2 for the stricter "drop `--yes`, pipe stdin" alternative.)* |

### FR-4 · Single bounded clarification round for a missing required field

**When** the assembled result is missing a required **gate** field (location or a valid
start date),
**the system shall** run **one bounded clarification round** in pure Python — one prompt per
missing required field (typically one; at most location + start) — re-validate the supplied
answers **once**, and proceed. The LLM is **not** re-invoked and does not loop.

**Where** a clarification answer is still invalid,
**the system shall** abort with a clear message — no second round, no LLM retry.

| AC | Criterion |
|----|-----------|
| FR-4-AC-1 | Prose with no location → Python detects the missing field and prompts once for a location; a valid answer assembles a complete `OpenRole` and the run proceeds. |
| FR-4-AC-2 | The clarification round makes **zero** additional LLM calls (it parses the operator's typed answer deterministically: a city string; an ISO date via `date.fromisoformat`). |
| FR-4-AC-3 | An invalid clarification answer aborts with a non-zero exit and a clear reason; there is no second round and no LLM retry. |
| FR-4-AC-4 | Only location/start trigger clarification; title, skills, and co-location are surfaced in the echo (FR-3) for confirmation, not clarified. |

### FR-5 · Route the call through `PseudonymisedLM` (pass-through, unscanned)

**When** the intake predictor calls the provider,
**the system shall** go through `pii/PseudonymisedLM` (Golden rule 3), invoked **without**
`pii_context` so it behaves as pass-through — role text describes the role, carries no
candidate identity, and must not be redacted (consistent with `clarify`, §7).

| AC | Criterion |
|----|-----------|
| FR-5-AC-1 | The live intake predictor is built over `PseudonymisedLM(model=models.reasoning_llm, temperature=0)` at the CLI composition root; `dsm.match` imports no `dsm.pii` (the `match ⊥ PII` contract, AD-101, stays green). |
| FR-5-AC-2 | The intake call runs **outside** any `pii_context`, so `PseudonymisedLM.__call__` short-circuits to pass-through **before any redaction or leak-scan runs** — the prose is forwarded to the provider **unscanned**. The non-PII property is an **operator-input assumption inherited from `clarify`/§7** (role text, not candidate identity), **not an enforced gate**. *(Deferred risk, recorded in `docs/backlog.md`: free-form `--query` invites an operator to type a name, a wider surface than the structured CSV cell; if a future path ever wraps intake in `pii_context`, it must adopt `score.py`'s class-name `PIILeakError`-propagation discipline, not swallow-and-abort.)* |

### FR-6 · Deterministic, version-pinned, cached parse

**When** the same prose is submitted on the same run-date under the same
`(model_id, prompt_version)`,
**the system shall** return the same `OpenRole` — guaranteed by `temperature = 0`, a pinned
derivation version `(models.reasoning_llm, nl_intake.prompt_version)` (AD-066), and a
content-addressed parse cache keyed by `sha256(normalised_prose | today | model_id |
prompt_version)`.

**When** the model id or `nl_intake.prompt_version` changes,
**the system shall** treat it as a derivation-version bump (AD-066): the cache key changes,
forcing a re-parse — never silent reuse of a stale parse. *(`nl_intake.prompt_version` MUST
be bumped whenever `config/prompts/role_intake.md` changes — the same operator discipline the
`enrich` block documents; recorded as an inline config comment.)*

| AC | Criterion |
|----|-----------|
| FR-6-AC-1 | A second identical `--query` invocation on the same run-date hits the cache: the intake predictor is called **once**, not twice. |
| FR-6-AC-2 | The cache key includes the run-date — a **deliberate** key term so a relative-date parse is never reused across days (no stale relative resolution). *(Trade-off, accepted: an absolute/date-less query re-parses each new day; the safe direction — never stale.)* |
| FR-6-AC-3 | Bumping `nl_intake.prompt_version` (or the model id) changes the cache key for the same prose (verified by the pure key function). |
| FR-6-AC-4 | **Determinism boundary (corrected):** the existing `determinism` Tier-1 invariant binds `run_match(candidates, scorecard, …)` — i.e. the pipeline **below the post-clarify `TargetProfileScorecard`**, not the `OpenRole`. `clarify` is an LLM stage that sits below the `OpenRole` on **both** front doors. NL therefore **inherits the CSV path's existing scorecard-level determinism guarantee unchanged** — it neither extends byte-determinism up to the `OpenRole` nor relaxes the invariant (which is not added to or changed). |

### FR-7 · Converge on the unchanged pipeline (intake replaces clarify's LLM pass on the NL path)

**When** an `OpenRole` is confirmed from prose,
**the system shall** feed it into the **same** `_run_role` core the CSV path uses, with
`demand_as_of = today` (the run-date) for the freshness guard (AD-087), a synthesized
deterministic `role_id`, the existing `render_identities` output step (AD-107), and — because
the intake LLM has already interpreted the free text — the **echo (deterministic) clarify
path** (`clarify_role(role, predict=None)`), so the NL door makes exactly **one** LLM
interpretation call (intake), not two.

| AC | Criterion |
|----|-----------|
| FR-7-AC-1 | The NL path and the CSV path share one `_run_role(role, demand_as_of, *, clarify_predict, …)`; gates/exact-filter/recall/rerank/score/rank are unchanged. CSV passes the live clarify predictor; **NL passes `clarify_predict=None`** → `clarify_role` takes its deterministic echo path and partitions the intake-classified `required_skills` by depth into the scorecard (the intake HARD/DESIRED split is authoritative; no second LLM re-derivation). |
| FR-7-AC-2 | `demand_as_of` for an NL query is the run-date. The freshness guard runs unchanged but, because `demand_as_of = today` and a validated NL `start_date ≥ today ≥ valid_as_of`, only **`ok` / `refuse`** are reachable — the `warn` branch (which needs `start_date < valid_as_of`) is **structurally unreachable** on the NL path. Consequence (documented, accepted): fixing `demand_as_of = today` makes NL **stricter** than a backdated CSV banner — identical supply that a backdated CSV serves `ok` can be `refuse`d via NL once staleness from today exceeds `reconcile.max_staleness_days`. |
| FR-7-AC-3 | The synthesized `role_id` is deterministic (`NL-<first 8 of the content hash>`) and appears in the output + lineage. |
| FR-7-AC-4 | `dsm match` requires **exactly one** of `--query` / `--role-id`; supplying both or neither errors with a clear message. `--role-id` becomes `Optional[str] = None` on `match`; existing keyword callers (`match(role_id=…)`) stay valid. `explain` is unchanged (`--role-id` only). |

---

## Non-functional requirements

| NF | Requirement |
|----|-------------|
| NF-1 | **No network / LLM in `make check`.** The intake predictor is an injected seam (`IntakePredictor`); unit + CLI tests inject a fake (mirrors `clarify`/`score`). The live predictor is built only at the CLI edge. CLI tests reuse the **full** `wired` seam set (clarify, score, embed, query-store, near-miss-rationale) **plus** the intake builder + a fake `IntakeCache`, so no live LM/Milvus/vault construction occurs offline. |
| NF-2 | **No frozen-contract change.** `RoleIntake` is a match-local type (`dsm/match/models.py`), like `ScorecardClarification`/`ScoreExtraction`. `dsm/models.py` (AD-060) is **untouched** — no `make contract-snapshot`. The parser produces the existing `OpenRole` (all required fields supplied; `preferred_skills`/`onsite_cities` left at their defaults). |
| NF-3 | **Config over constants** (tech.md rule 6). Model id reused from `config/default.yaml::models.reasoning_llm`; `nl_intake.{prompt_version,temperature,max_horizon_days,cache_dir}` added (each with rationale); prompt text in `config/prompts/role_intake.md`. No inline literals. |
| NF-4 | **Typed boundaries.** `RoleIntake` (Pydantic) is the signature output; assembly returns a typed result (a valid `OpenRole` or a typed `ClarificationNeeded`), never a dict-as-contract. |
| NF-5 | **Import boundaries hold.** `dsm/match/intake.py` imports **no** `dsm.pii`, `dsm.ingest`, `modal`, or `httpx` (the forbidden sets the import-linter actually polices). It may use `dspy`, `dsm.config`, `dsm.models`, `dsm.match.models`, `structlog`, and stdlib (`datetime`/`re`/`hashlib`/`typing`) as needed. The `PseudonymisedLM` and the file cache are injected from the CLI. |
| NF-6 | **Compileable signature, enforced.** The signature is plain (instruction in config, no baked few-shot), so it can later be optimised offline (MIPROv2). A test asserts the predictor is a bare `dspy.Predict` with config-loaded instructions and no baked demos (FR-1-AC-1), so the property is guarded, not just documented. Compilation itself is a follow-on. |
| NF-7 | **Deterministic eligibility (AD-002).** Gate **functions** stay pure Python. `co_location_required` is **Python-derived**, never an LLM output (FR-8). The remaining gate inputs the prose is the only source for (`location_city`, `start_date`) are **LLM-parsed proposals confirmed by a human** before gating — the eligibility boundary is the **confirmed** `OpenRole` (the A1 watch-out; consistent with AD-064). The byte-determinism *eval* boundary is the post-clarify scorecard (FR-6-AC-4). |

### FR-8 · Co-location is Python-owned, never an LLM gate decision *(blocker fix)*

**When** the system assembles the `OpenRole` from `RoleIntake`,
**the system shall** derive `co_location_required` **deterministically in Python** —
`co_location_required = bool(location_city) and not remote_within_country` — and **shall not**
accept `co_location_required` as an LLM output field. The derived value is shown in the echo
(FR-3-AC-1) for human confirmation.

> **Why (AD-002 / Golden rule 2).** `dsm/match/gates.py::_location_passes` branches decisively
> on `co_location_required` (and the role city) to decide who clears the location gate. If the
> LLM set `co_location_required` and `--yes` skipped review, an LLM value would decide
> eligibility — exactly what AD-002 forbids and what `clarify.py` already guards against
> (location/co-location/start "always come from the parsed role + config, never the model").
> Deriving it in Python mirrors that precedent. `location_city` remains an LLM-parsed *fact*
> (the prose's only source for it, analogous to the CSV `Location` column) gated by human
> confirmation; `co_location_required` is a *policy inference* and must be code-owned.

| AC | Criterion |
|----|-----------|
| FR-8-AC-1 | `RoleIntake` has **no** `co_location_required` field; `assemble_role` computes it as `bool(location_city) and not remote_within_country`. |
| FR-8-AC-2 | A prose role naming a city with no remote signal → `co_location_required = True`; a "remote (India)" role → `False`; no value the LLM emits can change this. |
| FR-8-AC-3 | Under `--yes`, the gate still receives the Python-derived `co_location_required` (no LLM-set gate value can reach the gate even when interactive review is skipped). |

---

## Acceptance / Definition of Done
- All FR acceptance criteria covered by tests.
- New fixtures: several real-style phrasings parse to the correct `OpenRole`, **including one
  relative-date case** (FR-2) and **one missing-location case** that triggers the single
  clarification round (FR-4).
- `make check` GREEN (unit + CLI tests, import contracts, `tests/docs`, Tier-1 eval — none relaxed).
- New decisions recorded in `docs/decision.md` (AD-XXX front door incl. co-location-Python-owned +
  intake-replaces-clarify; AD-XXY relative-date resolution + freshness ok/refuse-only on NL —
  placeholders until `/handoff-index` assigns real numbers at merge; footer notes next is AD-110).
- Lane file `docs/progress.C.md` updated; README documents `dsm match --query` (citing config keys,
  not restating values — doc hygiene).
- One task = one commit, each referencing this spec.

---

## Review fixes applied (pre-sign-off adversarial review)
- **Blocker** → FR-8: `co_location_required` is now Python-derived, never an LLM output; `--yes`
  reframed as an explicit human override that still echoes + still uses the Python-derived gate value.
- **Major** → FR-7-AC-2: corrected — NL freshness yields only `ok`/`refuse` (`warn` structurally
  unreachable); NL is stricter than a backdated CSV (documented).
- **Minor** → FR-6-AC-4 / NF-7: determinism boundary corrected to the post-clarify scorecard.
- **Design** → FR-7-AC-1: NL skips `clarify`'s LLM (echo path); intake is the single interpretation
  pass; `assemble_role` forces skill depth per bucket (removes the two-channel ambiguity).
- **Refinements** → FR-1-AC-2 (pin temperature explicitly — existing builders don't), FR-1-AC-5
  (forced depth + `min_proficiency`), FR-3-AC-1 (echo scope; `onsite_cities`/`preferred_skills`),
  FR-5-AC-2 (unscanned pass-through is an assumption, not a gate), FR-6 (prompt-version coupling),
  NF-5 (forbidden-set wording), NF-6 (compileability enforced by a test).

---

## Open questions for sign-off
1. **Sequence number** — C-006 chosen to preserve the `c-005` = AD-084 reservation; renumber to C-005 if the team would rather drop that reservation. *(Recommendation: keep C-006.)*
2. **`--yes` semantics** (FR-3-AC-4) — keep `--yes` as an explicit human pre-confirmation override (parsed role always echoed for audit; `co_location` Python-derived), or drop `--yes` entirely and require piped-stdin confirmation (`echo y | dsm match --query …`) for non-interactive use? *(Recommendation: keep `--yes` as specified — it is a human act, not an LLM decision, and the blocker is closed by FR-8; the stricter no-`--yes` option is available if the team wants zero bypass.)*
3. **Date resolution authority** — this slice has the **LLM resolve** relative→ISO (today injected) + **Python validate** (the literal A3 instruction). A fully-deterministic Python relative-date resolver (LLM extracts the phrase only) is the more robust long-term option but is **deferred** as a fast-follow. Confirm the LLM-resolves/Python-validates split for this slice. *(Recommendation: as specified; the confirmation echo + derivation-version pinning close the determinism loop.)*
