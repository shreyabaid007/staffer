# C-007 Query-Side Negation → Hard Location Filter — Requirements

> **Lane:** C · **Slice:** C-007 (workshop item **A2**) · **Builds on:** c-006 NL intake (AD-110/111)
> **Source:** `workshop-feedback/iteration-2-gaps-and-interview-questions.md` § A2 — "'not Chennai' is
> not reliably handled by vector similarity; requires explicit hard-filter logic."

---

## User story

As a **staffing manager**, when I type *"senior Kotlin engineer, **not Chennai**, starting next
month"*, the system records the exclusion as a **typed location exclusion** enforced in the
**pure-Python location gate** — never in the embedding/cosine query — so a negation reliably removes
the excluded candidates instead of being washed out by vector similarity.

This is **A2** from the workshop deck. It was deliberately deferred from c-006 because it **mutates
the frozen `OpenRole` contract** (AD-060) — this slice does that change properly (ADR + snapshot).

---

## Scope

### In scope
- A typed **`exclude_cities`** facet on the role, parsed from NL negation and enforced by the
  deterministic location gate (candidate is excluded if their **home city** is in the set).
- Frozen-contract amendment (AD-060): add `exclude_cities: frozenset[str] = frozenset()` to
  **`OpenRole`** (parser output) and **`TargetProfileScorecard`** (gate input); thread it through
  `clarify_role`. Additive + optional + defaulted → backwards-compatible; `make contract-snapshot`.
- NL parsing: the intake signature extracts "not <city>" / "anywhere but <city>" / "exclude <city>"
  into the set; shown in the confirmation echo; enforced by Python (the LLM never decides the gate).
- The CSV front door is unchanged (no negation column) — it defaults `exclude_cities` empty.

### Out of scope (deferred — their own ADRs)
- **`exclude_sectors`** / domain exclusions — no sector gate exists today (new dimension).
- **Candidate-level exclusion** ("anyone but consultant X", "not the people who rolled off account
  Y") — PII-adjacent + needs identity/account matching.
- **Skill negation** ("not Java") on the query side — candidate-side already handled (AD-072);
  query-side skill exclusion is a separate filter.
- **A4** bounded relaxation — unchanged.

---

## Functional requirements (EARS)

### FR-1 · Frozen-contract amendment: `exclude_cities`

**When** the contract is amended,
**the system shall** add `exclude_cities: frozenset[str] = frozenset()` to **`OpenRole`** and
**`TargetProfileScorecard`** (default empty → every existing construction stays valid), and
regenerate the frozen-contract snapshot.

| AC | Criterion |
|----|-----------|
| FR-1-AC-1 | `OpenRole(...)` and `TargetProfileScorecard(...)` accept `exclude_cities`; omitting it yields `frozenset()`. |
| FR-1-AC-2 | `make contract-snapshot` regenerated; `tests/docs` frozen-contract test green; the change is an ADR-backed reviewable diff. |
| FR-1-AC-3 | All existing `OpenRole`/`TargetProfileScorecard` constructions (CSV parse, clarify, tests, fixtures) keep validating with the default. |

### FR-2 · `clarify_role` threads the exclusion to the scorecard

**When** `clarify_role` builds the `TargetProfileScorecard` from an `OpenRole`,
**the system shall** copy `exclude_cities` from the role to the scorecard verbatim — like the other
gate fields (location / co-location / start), it comes from the parsed role, **never** the LLM.

| AC | Criterion |
|----|-----------|
| FR-2-AC-1 | Both the echo path and the LLM-refine path of `clarify_role` carry `exclude_cities` through unchanged. |
| FR-2-AC-2 | The clarify LLM cannot set, add to, or relax `exclude_cities` (it is a gate input, AD-002). |

### FR-3 · The location gate enforces the exclusion (pure Python, AD-002)

**When** the location gate evaluates a candidate,
**the system shall** exclude (reason `LOCATION_MISMATCH`) any candidate whose **home city** matches a
member of `scorecard.exclude_cities` (case-insensitive), **before** the positive location logic and
**regardless** of `co_location_required` — and this check stays in `dsm/match/gates.py` (no LLM, no
embedding).

| AC | Criterion |
|----|-----------|
| FR-3-AC-1 | Candidate home city ∈ `exclude_cities` → excluded with `LOCATION_MISMATCH`, even for a distributed (`co_location_required=False`) role. |
| FR-3-AC-2 | The exclusion matches on the candidate's **home `city`** only — `onsite_cities` (willingness) does not trigger it. |
| FR-3-AC-3 | Empty `exclude_cities` (the default, all CSV roles) → gate behaviour is **byte-identical** to today. |
| FR-3-AC-4 | The exclusion is never added to the recall/embedding query — it lives only in the gate (the A2 invariant: negation is a hard filter, not cosine). |
| FR-3-AC-5 | **An excluded candidate is NOT a near-miss.** An exclusion is non-negotiable, so `build_near_misses` must **skip** `LOCATION_MISMATCH` rows whose candidate home city ∈ `exclude_cities` — otherwise a deliberately-excluded candidate re-surfaces in the no-match `near_misses` list framed as "one decision away" (the c-006 hard-exclude-leaks-back pattern). |
| FR-3-AC-6 | The `LOCATION_MISMATCH` `Exclusion.detail` distinguishes an **exclusion** miss ("candidate is in `<city>`, which the role excludes") from an ordinary positive-location miss — `filter_candidates` re-checks `candidate.city ∈ exclude_cities` to pick the wording (the gate returns a bool). |

### FR-4 · NL intake parses negation into `exclude_cities`

**When** the prose states a location negation ("not Chennai", "anywhere but Chennai", "exclude
Chennai"),
**the system shall** extract the city into `RoleIntake.exclude_cities` (a new match-local field),
and `assemble_role` shall normalise + place it into `OpenRole.exclude_cities`; the parsed exclusion
shall appear in the **confirmation echo**.

| AC | Criterion |
|----|-----------|
| FR-4-AC-1 | "senior Kotlin engineer, not Chennai, starting next month" → `exclude_cities = {"chennai"}` as a **distributed role** (`location.city = None`, `co_location_required = False`) — an exclusion with **no positive city** means "anywhere but Chennai" and must assemble cleanly, **not** trigger a missing-location clarification. `kotlin` HARD. |
| FR-4-AC-1b | A missing-location clarification (FR-4 of c-006) fires only when there is **no** positive city, **not** remote, **and** `exclude_cities` is empty. A non-empty `exclude_cities` makes the location well-defined (distributed-minus-excluded). |
| FR-4-AC-2 | "React dev in Bengaluru, not Chennai" → `location.city = "Bengaluru"` (onsite), `exclude_cities = {"chennai"}` (positive + negative coexist). |
| FR-4-AC-3 | City names in `exclude_cities` are normalised to lowercase; the gate compares case-insensitively anyway. |
| FR-4-AC-4 | The echo lists `exclude_cities` (e.g. `excludes: chennai`) so the human confirms it before gating; absent → not shown / empty. |
| FR-4-AC-5 | The intake prompt instructs that a negation populates `exclude_cities` and is **never** a positive `location_city` — and (per c-006) absent ⇒ null/empty, never guessed. |

---

## Non-functional

| NF | Requirement |
|----|-------------|
| NF-1 | No network/LLM in `make check`; intake parsing tested with a fake predictor; the gate test is pure. |
| NF-2 | **Frozen-contract amendment is the only `dsm/models.py` change** — additive, ADR-backed, snapshot-regenerated. No other contract churn. |
| NF-3 | **Determinism + AD-002:** the LLM *proposes* `exclude_cities` (a parse value, confirmed via echo); the **gate enforces it in pure Python**. Gates import no LLM/embedding. Negation never reaches cosine (FR-3-AC-4). |
| NF-4 | Config/typed boundaries unchanged in spirit; `RoleIntake.exclude_cities` is match-local (not frozen). |

---

## Acceptance / Definition of Done
- FR acceptance criteria covered by tests: a **gate** test (exclude on home city, incl. distributed
  role; `onsite_cities` not triggered; empty default unchanged), an **intake** test (negation →
  `exclude_cities`; positive+negative coexist), and a **clarify** thread-through test.
- New NL-intake **eval** golden case with a negation (offline + live), extending c-007's
  `nl_intake_golden.json`.
- `make check` GREEN (incl. `tests/docs` frozen-contract snapshot regenerated); `make eval` green.
- New decision in `docs/decision.md` (`AD-XXX` placeholder — frozen-contract amendment; assigned at
  merge). Lane file updated. One task = one commit.

---

## Open questions for sign-off
1. **Scope** — `exclude_cities` only (recommended), with sectors / candidate-level / skill negation deferred to their own ADRs? *(Recommendation: yes — clean, one gate dimension, deterministic.)*
2. **Exclusion semantics** — exclude on the candidate's **home city** only (recommended), not their `onsite_cities` willingness. So "not Chennai" removes Chennai-*based* candidates, not those merely open to onsite there. Confirm. *(Recommendation: home-city only; it matches "don't staff a Chennai person".)*
3. **Frozen-contract amendment on two models** (`OpenRole` + `TargetProfileScorecard`) is required because the gate reads the scorecard — confirm you're OK amending both (both additive/defaulted). *(Recommendation: yes; it's the minimal honest change.)*
