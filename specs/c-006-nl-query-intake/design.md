# C-006 Natural-Language Query Intake — Design

> Implements `requirements.md`. Mirrors the established `clarify` pattern (a DSPy signature in
> `dsm/match/` with an injected predictor seam + a deterministic fallback) and the CLI
> composition-root wiring in `dsm/cli/commands.py`. **Produces the existing `OpenRole`** — the
> frozen spine (`dsm/models.py`, AD-060) is untouched.
> Incorporates the pre-sign-off adversarial review (FR-8 co-location, freshness ok/refuse, NL
> skips clarify's LLM, determinism-boundary wording, temperature pinning).

---

## 1. Where the front door sits

```
dsm match --role-id ROLE-X   ─┐                                       (existing CSV door)
                              ├─►  OpenRole  ──►  _run_role(role, demand_as_of, clarify_predict=LIVE, …)
dsm match --query "<prose>"  ─┘                  _run_role(role, today,        clarify_predict=None,  …)
                                   ▲                                  (NEW prose door — this slice)
                                   │  parse (LLM, 1 call) → assemble/validate (Python) → echo+confirm → [clarify-round if needed]
```

Both doors converge on a single shared `_run_role`. On the **CSV** door `clarify`'s LLM runs
(refines the structured parse from the Notes cell). On the **NL** door the **intake LLM is the
free-text interpretation step**, so `_run_role` is called with `clarify_predict=None` →
`clarify_role` takes its deterministic **echo** path (partition `required_skills` by depth). Net:
each door makes exactly **one** LLM interpretation call before the scorecard. Everything from the
scorecard onward (gate → exact filter → recall → rerank → score → rank → `render_identities`) is
byte-for-byte the current pipeline.

---

## 2. Modules touched / added

| Module | Change |
|--------|--------|
| `dsm/match/models.py` | **Add** `RoleIntake` (DSPy output type, match-local) — alongside `ScorecardClarification`/`ScoreExtraction`. **No** `co_location_required` field (FR-8). No frozen-contract change. |
| `dsm/match/intake.py` | **New.** `RoleIntakeSignature`, `make_intake_predictor(lm)`, the `IntakePredictor` seam type, the pure `assemble_role(...)` + relative-date validation + co-location derivation + forced skill-depth, `intake_cache_key(...)`, and the `IntakeCache` protocol + `NullIntakeCache`. Imports `dspy` + `dsm.config` + `dsm.models` + `dsm.match.models` + `structlog` + stdlib only (NF-5). |
| `dsm/cli/commands.py` | Refactor `_match_role` → extract shared `_run_role(role, demand_as_of, *, clarify_predict, …)`. Add `_match_query`, `_build_intake_predictor`, `FileIntakeCache`, the echo/confirm/clarify-round I/O, and the `--query`/`--yes` options + `--role-id` optionalisation + exactly-one validation on `match`. |
| `config/default.yaml` | **Add** `nl_intake: {prompt_version, temperature, max_horizon_days, cache_dir}` (with inline rationale + the prompt-version coupling comment). Model id reused from `models.reasoning_llm`. |
| `config/prompts/role_intake.md` | **New.** Intake signature instruction (incl. the verbatim "never guess" directive; proficiency-word mapping; "extract facts, never decide eligibility"). |
| `README.md` | Document `dsm match --query` (cite config keys, not values — doc hygiene). |
| `tests/match/test_intake.py`, `tests/cli/test_match_query.py` | **New.** |

---

## 3. Data contracts

### 3.1 `RoleIntake` (new, `dsm/match/models.py`) — the DSPy signature output
A match-local frozen Pydantic model (not a frozen `dsm/models.py` contract). All fields default
to `null`/empty so "absent ⇒ null, never guess" (FR-1) is representable. **No
`co_location_required`** — that is Python-derived (FR-8).

```python
class RoleIntake(BaseModel, frozen=True):
    """The NL intake signature's structured output (C-006; § A1/A3). Match-local DSPy output
    type — NOT a frozen contract. Python assembles + validates this into the existing OpenRole;
    the LLM only proposes/extracts parse facts (AD-002 — gate functions stay pure Python, and
    co_location_required is derived in code, never emitted by the LLM)."""
    title: str | None = None
    hard_skills: list[SkillRequirement] = Field(default_factory=list)   # reuse frozen type
    desired_skills: list[SkillRequirement] = Field(default_factory=list)
    location_city: str | None = None            # None ⇒ unspecified or remote (LLM-parsed fact)
    remote_within_country: bool = False          # maps to Location.remote_within_country (AD-086)
    start_date_iso: str | None = None            # LLM-resolved ISO (today injected); Python validates
    start_date_phrase: str | None = None         # original phrase, for the echo + logs
    notes: str | None = None                     # residual constraints → OpenRole.description
```
- `SkillRequirement` is reused verbatim from `dsm/models.py` — one model per fact. Its `depth`
  is mandatory, so the LLM emits a depth per element; `assemble_role` **forces** it from the
  bucket (FR-1-AC-5), so the two channels can never disagree.
- `min_proficiency` is best-effort (prompt maps explicit proficiency words, else `None`).

### 3.2 Assembly result (new, `dsm/match/intake.py`) — typed, not a dict
```python
class ClarificationNeeded(BaseModel, frozen=True):
    """Required gate fields the prose did not yield — drives the single Python clarification round."""
    missing: list[Literal["location", "start"]]   # bounded set (FR-4)
    partial: RoleIntake                            # everything parsed so far, for re-assembly

# assemble_role returns OpenRole (ready) | ClarificationNeeded (needs the one bounded round).
RoleAssembly = OpenRole | ClarificationNeeded
```

### 3.3 The signature + predictor
```python
class RoleIntakeSignature(dspy.Signature):
    """Parse a free-text staffing role request into a structured intake (config/prompts)."""
    request_text: str = dspy.InputField()
    today: str = dspy.InputField()               # injected ISO run-date — partial mitigation (A3)
    intake: RoleIntake = dspy.OutputField()

IntakePredictor = Callable[[str, date], RoleIntake]   # (prose, today) → RoleIntake (mocked in tests)
```
`make_intake_predictor(lm)` = a **bare** `dspy.Predict(RoleIntakeSignature.with_instructions(
load_prompt("role_intake")))` (no `.demos` baked in — NF-6/FR-1-AC-1), called under
`dspy.context(lm=lm)` with `today=today.isoformat()`. Single-shot — no `Refine`/`ReAct`/retry.
*(A nested-Pydantic OutputField with `list[SkillRequirement]` is proven viable: `clarify`'s
`RoleClarification.clarification: ScorecardClarification` already does exactly this.)*

### 3.4 Cache seam
```python
class IntakeCache(Protocol):
    def get(self, key: str) -> RoleIntake | None: ...
    def put(self, key: str, value: RoleIntake) -> None: ...

def intake_cache_key(prose: str, today: date, model_id: str, prompt_version: str) -> str:
    """sha256 over (normalised prose | today.isoformat() | model_id | prompt_version) — the AD-066
    derivation version. Pure; lives in intake.py. The run-date is a deliberate key term so a
    relative-date parse is never reused across days (FR-6-AC-2)."""
```
`NullIntakeCache` (no-op) is the default for the pure path/tests; `FileIntakeCache` (JSON under
`config nl_intake.cache_dir`) is injected at the CLI. `cache_dir` lands under the **already-gitignored**
`data/.cache/*` (verified in `.gitignore`), so it is never committed; a corrupt/unreadable entry is
treated as a miss (never crash). Prose is treated as non-PII by the same §7 assumption as `clarify`
(FR-5-AC-2) — not an enforced guarantee.

---

## 4. Control flow (CLI, `_match_query`)

```
1. config = load_config(); today = date.today()
2. model_id = config["models"]["reasoning_llm"]; pv = config["nl_intake"]["prompt_version"]
   key = intake_cache_key(prose, today, model_id, pv)
3. intake = cache.get(key)  ─ miss ─►  intake = predict(prose, today)  ─►  cache.put(key, intake)   (FR-6)
4. role_id = "NL-" + key[:8]                                                                          (FR-7-AC-3)
5. assembly = assemble_role(intake, today, max_horizon_days=cfg, role_id=role_id)                     (FR-2, FR-4, FR-8)
6. if isinstance(assembly, ClarificationNeeded):
       answers = {}
       for field in assembly.missing:    # ONE round, one prompt per missing gate field, pure Python, NO LLM (FR-4)
           answers[field] = typer.prompt(<bounded question for field>)
       assembly = assemble_role(<intake updated with parsed answers>, today, …)   # re-validate ONCE
       if isinstance(assembly, ClarificationNeeded): abort(non-zero, clear reason)  # no second round
7. role: OpenRole = assembly
8. echo(role)                                                       # ALWAYS printed, incl. under --yes (FR-3-AC-4)
   if not (yes or typer.confirm("Proceed with this role?")): abort (no pipeline)   (FR-3)
9. return _run_role(role, demand_as_of=today, clarify_predict=None, gold_dir, db_path, vault_path)    (FR-7)
```

`assemble_role(intake, today, *, max_horizon_days, role_id)` — pure, the testable core:
- **start**: `start_date_iso` → `date.fromisoformat` (catch `ValueError`); require
  `today <= d <= today + max_horizon_days`. Pass → use `d`. Fail/absent → `"start"` missing (FR-2).
- **location**: `location_city` present → `Location(city=lower(city), remote_within_country=remote)`.
  `location_city is None and remote_within_country` → `Location(city=None, remote_within_country=True)`
  (valid; remote needs no city). `location_city is None and not remote` → `"location"` missing.
  `onsite_cities` / `state` are never populated from prose (left at their defaults) — the echo says so.
- **co_location_required** (FR-8, **Python-derived, not LLM**): `bool(location_city) and not
  remote_within_country`. A named city ⇒ onsite unless remote stated. Shown in the echo; no LLM
  value can change it — this is the blocker fix (gate input is code-owned, mirroring `clarify.py`).
- **skills** (FR-1-AC-5): force depth per bucket — `[s.model_copy(update={"name": lower(s.name),
  "depth": HARD}) for s in hard_skills] + [… DESIRED … for s in desired_skills]` → `required_skills`.
  Empty is allowed (echo shows "no skills"); not a clarification trigger. *(Deliberate asymmetry vs
  the CSV door, which rejects empty Required Skills in `demand.py`; the NL door relaxes it — a
  business choice, not a contract change.)*
- **title** / **notes** → `OpenRole.title` (fallback `""`) / `OpenRole.description`.
- `preferred_skills` is left at its default `[]` (no consumer in `dsm/` today; desired skills ride
  in `required_skills` with `depth=DESIRED`, matching `demand.py`/`clarify`).

The clarification answers are parsed deterministically: a location string → city; an ISO date
string → `date.fromisoformat`. **No LLM call** on the clarification path (FR-4-AC-2).

---

## 5. Refactor: shared `_run_role`

Extract from the current `_match_role` everything from `store = GoldCandidateStore(...)` onward
into:
```python
def _run_role(role: OpenRole, demand_as_of: date, *, clarify_predict, gold_dir, db_path, vault_path)
        -> tuple[ShortlistResult | NoMatchResult, Vault]
```
- `clarify_predict` is the injected clarify seam: **CSV** passes `_build_clarify_predictor(config)`
  (live LLM refine); **NL** passes `None` → `clarify_role` echoes (FR-7-AC-1).
- `_match_role` (CSV): `parse_demand` → select `role_id` → `_run_role(role, outcome.banner.demand_as_of,
  clarify_predict=_build_clarify_predictor(config), …)`. **Behaviour-preserving** for `dsm match --role-id`.
- `_match_query` (NL): parse/assemble/echo/confirm → `_run_role(role, today, clarify_predict=None, …)`.

This keeps the freshness guard, `run_match`, and `render_identities` identical for both doors
(FR-7-AC-1). `demand_as_of = today` for NL (FR-7-AC-2 — note the `ok`/`refuse`-only consequence).

`match(...)` signature changes:
```python
role_id: Annotated[str | None, typer.Option("--role-id")] = None   # WAS required; now Optional
query:   Annotated[str | None, typer.Option("--query")]   = None
yes:     Annotated[bool,       typer.Option("--yes")]      = False
# Require exactly one of --query / --role-id (FR-7-AC-4); error + non-zero exit otherwise.
# Existing keyword callers match(role_id=…) stay valid (Optional default None). explain() unchanged.
```

---

## 6. PII boundary (FR-5)

`_build_intake_predictor(config)` (a CLI builder, monkeypatched in tests, like
`_build_clarify_predictor`) constructs `make_intake_predictor(PseudonymisedLM(
model=config["models"]["reasoning_llm"], temperature=0))`. **Temperature is pinned explicitly**
here (FR-1-AC-2): the existing clarify/score builders do **not** pass a temperature kwarg (they
rely on the dspy default; "mirror clarify" applies to *shape*, not to temperature) — intake must
pin it. The call runs **outside** any `pii_context`, so `PseudonymisedLM.__call__` short-circuits
to pass-through *before* redaction/leak-scan (FR-5-AC-2 — unscanned; non-PII is an inherited
assumption, not a gate). `dsm/match/intake.py` never imports `dsm.pii`; the `match ⊥ PII` contract
(AD-101) stays green.

---

## 7. Edge cases

| Case | Handling |
|------|----------|
| Both location & start missing | One clarification round prompts for **both** (bounded set), re-validates once. Still one round, zero extra LLM calls. |
| LLM resolves a relative date to a past/absurd ISO | Plausibility window rejects → `start` clarification (FR-2-AC-3). |
| LLM returns malformed `start_date_iso` (`"next month"`) | `date.fromisoformat` raises → caught → `start` clarification (FR-2-AC-2). |
| LLM hallucinates a city not in the prose | "never guess" instruction (FR-1-AC-3); the echo lets the human catch a residual misparse (FR-3-AC-1). |
| LLM emits a co-location opinion | Ignored — `RoleIntake` has no such field; co-location is Python-derived (FR-8). |
| Empty `required_skills` | Allowed; echo shows "no skills" — not a clarification trigger (FR-4-AC-4). Differs from the CSV door (which rejects empties in `demand.py`) — intentional. |
| Predictor raises (LLM error) | Abort with a clear message (no echo, no pipeline). No deterministic echo fallback (unlike `clarify`) — without a parse there is no `OpenRole`. Because `pii_context` is **unset**, no leak-scan fires, so the generic abort-on-error is sufficient here; if a future path wraps intake in `pii_context`, it must adopt `score.py`'s class-name `PIILeakError`-propagation discipline. |
| Both `--query` and `--role-id`, or neither | Error + non-zero exit (FR-7-AC-4). |
| `--yes` on a missing-required-field role | Clarification still runs (it precedes confirmation); `--yes` only pre-confirms the final review, and the role is still echoed for audit (FR-3-AC-4). |
| Cache file corrupt / unreadable | Treat as a miss (re-parse); never crash on cache I/O. |

---

## 8. Eval / test cases to add

**`tests/match/test_intake.py`** (pure, fake predictor — no network, NF-1):
- `assemble_role` over several real-style phrasings → correct `OpenRole` (title, location,
  Python-derived co-location, forced-depth skills, start_date). Fixtures (the "say it out loud"
  phrasings, § A1/§12):
  1. *"senior Kotlin engineer in Chennai, payments, starting next month"* → Chennai,
     `co_location_required=True` (derived), `kotlin` HARD, start = today+~1mo (relative-date, **FR-2**).
  2. *"React dev, remote India, available now"* → `remote_within_country`, no city,
     `co_location_required=False` (derived), start ≈ today.
  3. *"Lead consultant for a Bengaluru data platform — Spark and Airflow, onsite, start in 3 weeks"*.
  4. *"backend engineer, Java and Spring Boot, starting 2026-08-01"* with **no location** →
     `ClarificationNeeded(missing=["location"])` (**FR-4 missing-location case**).
- **FR-8**: co-location is derived, not taken from the LLM (a fixture whose `RoleIntake` could only
  influence it via the absent field proves it can't); named-city→True, remote→False (FR-8-AC-2).
- Relative-date validation: malformed ISO → `start` missing (FR-2-AC-2); out-of-window ISO →
  `start` missing (FR-2-AC-3); valid ISO → used (FR-2-AC-1).
- Forced skill depth (FR-1-AC-5): a `hard_skills` element carrying `depth=DESIRED` still lands HARD.
- "Never guess": `location_city=None` does not invent a city (FR-1-AC-4).
- `intake_cache_key` stability: same inputs → same key; changed `prompt_version`/model/today →
  different key (FR-6-AC-2/3).
- Prompt file contains the "never guess" directive (FR-1-AC-3).
- **Builder tests**: `make_intake_predictor` returns a bare `dspy.Predict` with no `.demos` (NF-6);
  `_build_intake_predictor` pins `temperature=0` on the LM — assert via a fake `PseudonymisedLM`
  subclass that records the kwarg (FR-1-AC-2).

**`tests/cli/test_match_query.py`** (reuse the **full** `wired` seam set from
`tests/cli/test_orchestrator.py` — clarify/score/embed/query-store/near-miss — **plus**
`_build_intake_predictor` (fake) + a fake `IntakeCache`, so nothing live is constructed offline,
NF-1):
- Happy path: `--query` + `--yes` → echo printed, shortlist JSON printed, `demand_as_of = today`,
  `role_id` starts `NL-` (FR-3, FR-7).
- Missing-location clarification: fake predictor returns no city; CliRunner `input="Chennai\ny\n"`
  → prompted once, assembles, runs (FR-4-AC-1); assert the intake predictor was called **once** (no LLM loop).
- Decline at confirm (`input="n\n"`, no `--yes`) → aborts, no shortlist (FR-3-AC-2).
- Cache hit: two identical `--query` calls same-day → intake predictor invoked once (FR-6-AC-1).
- `--query` + `--role-id` together (and neither) → error exit (FR-7-AC-4).
- Freshness on NL: a stale-enough supply → `refuse` exit; a fresh supply → shortlist (FR-7-AC-2);
  assert no `warn` flag is produced on the NL path (warn is unreachable).

**Eval invariants:** none added, none relaxed. The `determinism` Tier-1 invariant continues to
bind `run_match(candidates, scorecard, …)` — below the post-clarify scorecard, by construction
out of the NL parse layer's scope (FR-6-AC-4). (An NL-parse Tier-2/3 regression cassette is a
sensible fast-follow, out of scope here.)

---

## 9. ADRs (placeholders — `/handoff-index` assigns real numbers at merge; footer: next AD-110)

- **AD-XXX · Natural-language intake front door** — A prose `--query` front door parses free
  text into the **existing** `OpenRole` via a single-shot bare `dspy.Predict` (temp 0, pinned
  explicitly) over `PseudonymisedLM` (pass-through, unscanned — role text is non-PII by the §7
  assumption), with a confirmation echo before gating and a single bounded Python clarification
  round for a missing required gate field (no LLM loop, no `Refine`/ReAct). **`co_location_required`
  is Python-derived (`bool(location_city) and not remote_within_country`), never an LLM output** —
  gate inputs stay code-owned (AD-002, mirroring `clarify.py`); `location_city`/`start_date` are
  LLM-parsed facts gated by human confirmation, so the eligibility boundary is the **confirmed**
  `OpenRole`. On the NL path the intake LLM **replaces** `clarify`'s LLM pass — `_run_role` runs
  with `clarify_predict=None` (echo), so exactly one LLM interpretation call occurs and the intake
  HARD/DESIRED split is authoritative. NL `demand_as_of = run-date`; `role_id = "NL-<hash[:8]>"`;
  `--yes` is an explicit human pre-confirmation (still echoes; gate value still Python-derived).
  **No frozen-contract change** (produces `OpenRole`; A2 `exclude_*` deferred). Determinism via
  `temperature=0` + content-hash parse cache + pinned `(model_id, prompt_version)` derivation
  version (AD-066); the byte-determinism *eval* boundary remains the post-clarify scorecard.
  Rejected: a chat/ReAct loop (non-agentic invariant); `dspy.Refine` (temp 1.0); the LLM setting
  `co_location_required` (would let an LLM decide eligibility — the confirmed blocker); running
  `clarify`'s LLM as a second pass on NL (redundant; risks silent desired→hard strengthening).
- **AD-XXY · Relative-date resolution + NL freshness semantics** — The run-date is injected into
  the signature; the LLM resolves a relative phrase to an ISO date; Python validates it
  deterministically (calendar validity + a `[today, today+max_horizon]` plausibility **sanity
  bound**) **before** it reaches the availability gate; absent/malformed/implausible → the single
  clarification. Because NL pins `demand_as_of = today` and a validated `start_date ≥ today`, the
  freshness guard yields only **`ok`/`refuse`** on the NL path (`warn` is structurally unreachable),
  and NL is **stricter** than a backdated CSV banner (accepted). Rejected for this slice:
  fully-deterministic Python relative-date resolution (more robust; deferred as a fast-follow —
  honours the A3 "inject today + validate" instruction; the echo + derivation pinning close the loop).
