# Requirements — c-002 Query-Time Eval Harness

> EARS-form acceptance criteria. Each references a product invariant (`product.md`),
> the arch eval ownership (§12 #9), and the B-2 eval-case list
> (`specs/b-002-query-scoring-orchestration/design.md` § "Eval cases to add").
> Machine-verifiable where possible.

---

## R-01 · gates-respected invariant

**WHEN** the eval harness runs the `gates-respected` evaluator over a `ShortlistResult`,
the system **SHALL** assert that no candidate present in `exclusion_log` (with any
`ExclusionReason`) appears in `ranked_assessments`. Specifically for the golden cases:
ROLE-01 Aarav (`aarav@example.com`) is excluded on availability; ROLE-02 only
Chennai-based / Chennai-open candidates are present.

**WHEN** the evaluator is given a tampered `ShortlistResult` where a gated candidate has
been injected into `ranked_assessments`, the system **SHALL** return `passed=False` with
a reason that names the offending candidate and the exclusion reason.

## R-02 · hard-skill-not-cleared-by-adjacency invariant

**WHEN** the eval harness runs the `hard-skill-not-cleared-by-adjacency` evaluator, the
system **SHALL** assert that a candidate missing a hard skill but adjacent to it (per
`config/adjacency_map`) appears in `exclusion_log` with `HARD_SKILL_MISMATCH` and is
never ranked. Specifically for ROLE-01: Suresh (`suresh@example.com`) has `java` (adjacent
to required `kotlin`) but not `kotlin` → excluded with `HARD_SKILL_MISMATCH`.

**WHEN** given a tampered result where such a candidate is ranked, the system **SHALL**
return `passed=False`.

## R-03 · evidence-cited invariant

**WHEN** the eval harness runs the `evidence-cited` evaluator, the system **SHALL** assert
that every `CandidateAssessment.evidence[].text` is verbatim-present (whitespace-normalised)
in the candidate source text (skills + feedback + profile_summary). Reuses the AD-073
verify approach from `dsm/match/score.py`.

**WHEN** given a tampered result where one `evidence[].text` is mutated to a string absent
from the source, the system **SHALL** return `passed=False` naming the assessment and the
unverifiable quote.

## R-04 · no-PII-leak invariant (structural)

**WHEN** the eval harness runs the `no-PII-leak` evaluator, the system **SHALL** assert
that the inputs handed to the `predict`/embed/rerank seams and the final output contain
only `candidate_id`-level identifiers and capability text — no raw `name`/`email`.

Because `PseudonymisedLM` is still a pass-through stub (Lane C, `docs/progress.C.md`),
the invariant verifies **structurally only**: seam inputs are capability/`candidate_id`-only.
It does **not** yet exercise a real anonymiser. This limitation is stated in the invariant's
docstring and the design, with a TODO to tighten when the live anonymiser lands.

**WHEN** given a tampered result where a raw name appears in the narrative, the system
**SHALL** return `passed=False`.

## R-05 · determinism invariant (ordering/seed)

**WHEN** the eval harness runs the `determinism` evaluator, the system **SHALL** assert
**ordering- and seed-invariance**: shuffle the candidate-list order, vary dict/set insertion
order, re-seed, and assert the `ShortlistResult` is byte-identical (same ranking, tie-breaks,
flags, combine arithmetic). The cassette holds the LLM fixed; this isolates the plumbing.

The evaluator **SHALL NOT** test cassette-replay equality (tautological: a fixed cassette +
pure Python is identical by construction). The docstring **SHALL** state that the cassette
tier does not test live-model `temperature=0` reproducibility — that is a Tier-3 concern
(the cassette drift-guard).

## R-06 · adjacency-flag invariant

**WHEN** the eval harness runs the `adjacency-flag` evaluator, the system **SHALL** assert
that `ADJACENCY_USED` is present in `CandidateAssessment.flags` **if and only if**
adjacency credit (0.5) was awarded to some desired skill. Specifically for ROLE-01:
scorecard has `desired_skills=[java]`; Karan has `kotlin` (adjacent to `java` per
`adjacency_map`) → gets adjacency credit (0.5) → `ADJACENCY_USED` flag present.

**WHEN** given a tampered result where adjacency credit was awarded but the flag is absent
(or vice-versa), the system **SHALL** return `passed=False`.

---

## R-07 · golden cases + cassette LM

**WHEN** the eval harness runs, the system **SHALL** provide golden cases for ROLE-01,
ROLE-02, ROLE-03, and at least one negative (no-match) case, each binding a seed role
(from `tests/fixtures/`) to recorded `clarify`/`score` LM responses (cassettes) and the
expected `ShortlistResult` or `NoMatchResult`.

Cassettes **SHALL** be checked into git at
`tests/fixtures/cassettes/<case_id>/{clarify,score}.json`, keyed by `(case_id, signature,
prompt_hash, model_version)`.

## R-08 · cassette discipline

**WHEN** a cassette's key no longer matches the current prompt/model version, the Tier-1
test **SHALL** fail with a "stale cassette — re-record" message. It **SHALL NOT** fall
back to a live call or pass on the old recording. A missing cassette **SHALL** be a hard
error, not a skip.

A regeneration command (`make eval-record`) **SHALL** re-run the live LM over the golden
cases and rewrite the cassettes. Regeneration is explicit and reviewed in the diff — never
automatic, never inside a test run.

## R-09 · Tier-1 deterministic runner

**WHEN** `make check` runs, the system **SHALL** include the Tier-1 invariant evals
(`tests/eval/test_invariants.py`, marked `@pytest.mark.eval_offline`). These use the
cassette LM, touch no network, need no keys.

## R-10 · Tier-2 signature regression

**WHEN** `make eval` runs, the system **SHALL** include Tier-2 signature regression tests
(`tests/eval/test_signatures.py`, marked `eval_offline`) that pin `clarify` and `score`
DSPy signatures against fixed inputs: sub-scores ∈ [0,1], a citation present, hard skill
never credited via adjacency in the raw LLM output.

## R-11 · Tier-3 live smoke + drift guard

**WHEN** `make eval` runs with API keys present, the system **SHALL** include a Tier-3 live
smoke test (`tests/eval/test_live_smoke.py`, marked `eval_live`) that runs one real-LLM pass
over a seed role and asserts a well-formed `ShortlistResult`.

**WHEN** API keys are absent, the Tier-3 tests **SHALL** skip cleanly (never red on
key-less CI).

A Tier-3 drift-guard test **SHALL** re-record into a temp dir and diff against committed
cassettes, flagging when the live model has drifted from the deterministic tier's recording.

## R-12 · no live provider in `make check`

**WHEN** a test marked `eval_offline` runs, `tests/eval/conftest.py` **SHALL** provide an
autouse guard that patches `ModalEmbedClient` and the OpenRouter LM constructor to
**raise `RuntimeError`** if instantiated or called. A test proves the guard fires.

## R-13 · `make eval` wiring

**WHEN** `make eval` is invoked, the system **SHALL** run
`uv run pytest tests/eval -m "eval_offline or eval_live"` (Tiers 1–3). The `exit 1` stub
**SHALL** be replaced.

**WHEN** `make check` is invoked, the system **SHALL** additionally collect
`tests/eval/test_invariants.py` (Tier-1, `eval_offline`).

**WHEN** `make check-all` is invoked, the system **SHALL** run `make check` + `make eval`.

## R-14 · fixture enrichment

**WHEN** the eval harness runs, the seed fixtures (`tests/fixtures/__init__.py`) **SHALL**
carry short hand-authored resume snippets, feedback entries with populated `FeedbackSignals`,
and `profile_summary` text, so that `evidence-cited` can verify real quotes and `score` has
real input. Existing gates/rank tests **SHALL** still pass.

## R-15 · deliberately-failing fixtures

**WHEN** the eval harness runs, each of the six invariants **SHALL** have at least one
deliberately-failing fixture (a tampered `ShortlistResult` that violates the invariant), and
the test asserts the evaluator returns `passed=False` with a descriptive reason. This proves
the evaluator detects breakage, not just absence.

## R-16 · pytest markers registered

**WHEN** pytest discovers tests, the markers `eval_offline` and `eval_live` **SHALL** be
registered in `pyproject.toml` (`[tool.pytest.ini_options] markers`) without warnings.
`eval_live` tests carry `@pytest.mark.skipif(not _has_keys(), ...)`.

## R-17 · promptfoo packaging resolved

**WHEN** AD-095 is ratified, the `promptfoo` PyPI placeholder (0.1.4) **SHALL** be resolved
per the decision: if option (a) (recommended — drop PyPI dep, pytest-only), it is removed
from `pyproject.toml` and `docs/tech.md` §Eval is updated. `docs/tech.md` §Eval reflects
the three-tier model.

## R-18 · docs updated

**WHEN** the slice is complete, `docs/progress.C.md` **SHALL** be updated via `/handoff`,
`docs/decision.md` **SHALL** carry AD-095/094, and `dsm/eval/README.md` **SHALL** describe
the tier model + how to run.
