# Design — c-004 AI Eval Layer

> How C-4 is built. Adds the model-quality evaluation tier that the c-002
> deterministic invariants cannot cover: faithfulness (does the narrative follow
> from the evidence?), retrieval quality (does the pipeline surface the right
> candidates?), and ranking quality (does it order them correctly?). All
> non-gating — runs under `make eval` only. References: `ee-query-architecture.md`
> §9, AD-095 "deferred: narrative-faithfulness judge", c-002 design §"Out of scope".

---

## Design stance (decided)

- **Human labels are the ground truth.** An LLM judge is only as trustworthy as
  its validation against human labels. The golden set is drafted by machine from
  pipeline behaviour + fixture analysis, but **production trust requires human
  sign-off**. The judge is validated — not adopted — until labels are signed off.
- **Don't LLM-judge what code can check.** The six c-002 invariants (gates, PII,
  citation presence, hard-skill-exclusion, determinism, adjacency-flag) are
  objective. The faithfulness judge covers the **subjective residual**: does the
  narrative faithfully represent the evidence without fabrication or contradiction?
- **Retrieval metrics are meaningful only with recall ON** (AD-089). When recall
  is OFF (exhaustive passthrough), every candidate survives retrieval — Recall@K
  is trivially 1.0 and precision is meaningless.
- **Everything non-gating.** `make check` is untouched. New tests live in
  `tests/eval/` with `eval_live` markers (key-gated `skipif`). The deterministic
  Recall@K computation (no LLM) uses `eval_offline` but still runs only under
  `make eval`.

---

## ADRs to ratify (T-000-ADR gate — stop for sign-off)

### AD-104 — Hand-labelled golden set for AI-quality evals

**Decision:** Create a hand-labelled golden set (`tests/fixtures/golden_set.json`,
20–40 cases) binding seed roles to expected shortlists, expected relevant sets
(for Recall@K), and per-candidate faithfulness labels (y/n). The file carries
`_meta.review_status: "draft"|"signed_off"` — all eval tests that depend on
label correctness (judge validation, metric reporting) gate on
`review_status == "signed_off"` and skip otherwise.

**Labels are drafted by machine** from fixture definitions + pipeline output
analysis (deterministic invariants already tell us who passes gates, who is
excluded, and what the ordered shortlist looks like under the cassette LM).
**They are not trusted until a human reviews and signs off** — generated labels
carry the risk of encoding pipeline bugs as ground truth.

Why: the narrative-faithfulness judge (AD-095 deferred) needs labelled validation
data with TPR/TNR ≥ 80% to be adopted; Recall@K needs a relevant set;
ranking metrics need expected orderings. Without labels these metrics are
either unvalidatable or unmeasurable.

### AD-105 — DeepEval G-Eval faithfulness judge (validated, non-gating)

**Decision:** Wire a `deepeval.metrics.GEval` faithfulness judge that scores
whether a candidate narrative follows from the cited evidence + candidate data.
The judge is **validated against the golden set** — adopted only if TPR ≥ 0.80
and TNR ≥ 0.80 against signed-off human labels; if it fails validation, the
test skips (not fails). The judge **SHALL NOT** evaluate objective properties
already covered by deterministic invariants (gates, PII, citations, adjacency,
hard-skill-exclusion, determinism) — the LLM judge covers only the subjective
residual: fabrication, contradiction, and consistency between narrative and
sub-scores.

Runs under `make eval` only (`eval_live`, key-gated `skipif`). **Never in
`make check`** (non-deterministic + costs keys). The six deterministic
invariants remain the commit gate.

Why: an unvalidated LLM judge is worse than no judge — it adds cost and false
confidence. Validation-first (TPR/TNR ≥ 80%) ensures the judge earns its place
before results are reported. AD-095 deferred this until labelled data existed;
AD-104 provides it.

### AD-106 — Retrieval Recall@K + contextual precision (non-gating)

**Decision:** Compute deterministic Recall@K and contextual precision over the
golden set's `expected_relevant_set`. Recall@K = |retrieved ∩ relevant| / |relevant|
where K = `index.rerank.top_k`. Contextual precision = |relevant ∩ top-K| / K.
Computed over the post-rerank set (the candidates that enter scoring).
Additionally, run `deepeval.metrics.ContextualRecallMetric` /
`ContextualPrecisionMetric` for the LLM-judged retrieval view (signed-off labels
only).

Skipped when `index.recall.enabled = false` (trivially 1.0). Runs under
`make eval` only. Why: hybrid recall is ON (AD-089 default `true`), so retrieval
quality is now a meaningful axis; without metrics on it, retrieval regressions
are invisible.

---

## Modules

### `tests/fixtures/golden_set.json` (NEW)

The labelled golden set. Format:

```json
{
  "_meta": {
    "labeller": "machine-draft (pipeline analysis)",
    "label_date": "2026-06-23",
    "review_status": "draft",
    "notes": "Machine-generated from fixture analysis. REQUIRES HUMAN SIGN-OFF before judge validation or metric reporting."
  },
  "cases": [
    {
      "case_id": "GS-ROLE-01-shortlist",
      "role_fixture": "ROLE-01",
      "description": "Kotlin dev, Chennai co-location — 4 pass gates+skills, 1 excluded avail, 1 excluded hard-skill",
      "expected_shortlist": ["karan_cid", "rahul_cid", "vivaan_cid", "vikram_cid"],
      "expected_relevant_set": ["karan_cid", "rahul_cid", "vivaan_cid", "vikram_cid"],
      "faithfulness_labels": {
        "karan_cid": true,
        "rahul_cid": true,
        "vivaan_cid": true,
        "vikram_cid": true
      }
    },
    {
      "case_id": "GS-ROLE-03-no-match",
      "role_fixture": "ROLE-03",
      "description": "Java dev, Mumbai co-location — all excluded (avail/location)",
      "expected_shortlist": [],
      "expected_relevant_set": ["sanjay_cid", "meera_cid", "arjun_cid", "kavita_cid"],
      "faithfulness_labels": {}
    }
  ]
}
```

**Case count target: 20–40.** Built from:
- The 3 existing seed roles (ROLE-01/02/03) → 3 base cases.
- Variations on each: perturbed skill sets, shifted availability dates,
  relaxed/tightened location gates, added/removed candidates → ~12–15 variants.
- New fixture roles (ROLE-04 through ROLE-06) covering edge cases: all-adjacent
  (no exact hard-skill match), single-candidate pool, tie-breaking scenarios,
  remote-only roles → ~5–10 cases.
- Negative / adversarial: injected fabricated evidence, contradictory narratives,
  swapped candidate assessments → ~5 cases for faithfulness labelling.

**Generating the cases:** The draft is built by a task (T-002) that:
1. Runs `run_match` over each role fixture with the cassette LM.
2. Records the output (who passes gates, who is ranked, in what order).
3. For faithfulness: marks each narrative as `true` (all cassette narratives
   cite real evidence by construction) and creates negative cases by tampering
   the narrative/evidence to introduce fabrication.
4. For retrieval: records the post-rerank candidate set as the
   `expected_relevant_set`.

The human reviewer then validates each label and may correct ordering or
faithfulness judgements based on domain knowledge.

### `dsm/eval/golden_set.py` (NEW)

Typed loader for the golden set.

```python
from __future__ import annotations

from pathlib import Path
from pydantic import BaseModel, Field

class GoldenSetMeta(BaseModel):
    labeller: str
    label_date: str
    review_status: str  # "draft" | "signed_off"
    notes: str = ""

class GoldenSetCase(BaseModel):
    case_id: str
    role_fixture: str
    description: str = ""
    expected_shortlist: list[str]
    expected_relevant_set: list[str]
    faithfulness_labels: dict[str, bool] = Field(default_factory=dict)

class GoldenSet(BaseModel):
    meta: GoldenSetMeta = Field(alias="_meta")
    cases: list[GoldenSetCase]

    @property
    def is_signed_off(self) -> bool:
        return self.meta.review_status == "signed_off"

def load_golden_set(path: Path | None = None) -> GoldenSet:
    """Load and validate the golden set from JSON."""
    ...
```

### `dsm/eval/faithfulness.py` (NEW)

G-Eval faithfulness judge + validation harness.

```python
from __future__ import annotations

from dataclasses import dataclass
from deepeval.metrics import GEval
from deepeval.test_case import LLMTestCase

@dataclass(frozen=True)
class FaithfulnessVerdict:
    candidate_id: str
    score: float       # 0.0–1.0
    faithful: bool     # score >= threshold
    reason: str

@dataclass(frozen=True)
class JudgeValidation:
    tpr: float
    tnr: float
    adopted: bool      # tpr >= 0.80 and tnr >= 0.80
    details: str

FAITHFULNESS_CRITERIA = """Evaluate whether the narrative assessment of the candidate
faithfully represents the evidence and candidate data provided. Consider:
1. Does the narrative make any claims not supported by the cited evidence?
2. Does the narrative contradict the candidate's skills, feedback, or profile?
3. Is the characterisation of skill fit consistent with the sub-scores?

Do NOT evaluate: whether gates are correctly applied (location/availability
filtering), whether PII is present, whether citations exist, or whether the
candidate should have been excluded — these are checked by separate deterministic
tests.

Score 1 if the narrative is faithful, 0 if it fabricates or contradicts."""

def build_faithfulness_judge() -> GEval:
    """Construct the G-Eval faithfulness metric."""
    return GEval(
        name="Narrative Faithfulness",
        criteria=FAITHFULNESS_CRITERIA,
        evaluation_params=[
            "input",           # role + candidate context
            "actual_output",   # the narrative
        ],
        threshold=0.5,
    )

def judge_narrative(
    narrative: str,
    candidate_context: str,
    role_context: str,
) -> FaithfulnessVerdict:
    """Run the G-Eval faithfulness judge on a single narrative."""
    ...

def validate_judge(
    predictions: list[tuple[str, bool]],  # (candidate_id, judge_says_faithful)
    labels: dict[str, bool],              # candidate_id → human_label
) -> JudgeValidation:
    """Compute TPR/TNR of the judge against human labels."""
    ...
```

**Implementation notes:**

- The `GEval` metric is configured with a focused criteria string that
  explicitly excludes the six deterministic invariant concerns.
- `build_faithfulness_judge()` constructs the metric once; `judge_narrative()`
  applies it per candidate assessment.
- `validate_judge()` computes TPR = TP/(TP+FN) and TNR = TN/(TN+FP) where
  TP = judge says faithful AND human says faithful, etc.
- The threshold for the G-Eval score → `faithful: bool` conversion is tuned
  during validation (start at 0.5, adjust if TPR/TNR are close to 0.80).

### `dsm/eval/retrieval_quality.py` (NEW)

Deterministic Recall@K + contextual precision, plus DeepEval wrappers.

```python
from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class RetrievalMetrics:
    recall_at_k: float          # |retrieved ∩ relevant| / |relevant|
    contextual_precision: float  # |relevant ∩ top_k| / k
    k: int
    retrieved_ids: list[str]
    relevant_ids: list[str]

def compute_recall_at_k(
    retrieved_ids: list[str],
    relevant_ids: list[str],
    k: int,
) -> float:
    """Deterministic Recall@K. No LLM, no keys."""
    top_k = set(retrieved_ids[:k])
    relevant = set(relevant_ids)
    if not relevant:
        return 1.0  # vacuously true
    return len(top_k & relevant) / len(relevant)

def compute_contextual_precision(
    retrieved_ids: list[str],
    relevant_ids: list[str],
    k: int,
) -> float:
    """Deterministic contextual precision."""
    top_k = set(retrieved_ids[:k])
    relevant = set(relevant_ids)
    if not top_k:
        return 0.0
    return len(top_k & relevant) / len(top_k)

def compute_retrieval_metrics(
    retrieved_ids: list[str],
    relevant_ids: list[str],
    k: int,
) -> RetrievalMetrics:
    """Bundle Recall@K + contextual precision."""
    ...
```

**DeepEval integration:** The `eval_live` test additionally runs
`ContextualRecallMetric` and `ContextualPrecisionMetric` from DeepEval,
which provide LLM-judged retrieval quality (does the retrieval context contain
the information needed to answer correctly?). These need signed-off labels.

### `tests/eval/test_faithfulness.py` (NEW)

```python
@pytest.mark.eval_live
@pytest.mark.skipif(not _has_keys(), reason="No API keys for faithfulness judge")
class TestFaithfulnessJudge:
    def test_judge_on_golden_faithful(self, golden_set, shortlist_result):
        """Judge should score faithful narratives high."""

    def test_judge_on_tampered_fabrication(self, golden_set):
        """Judge should score fabricated narratives low."""

@pytest.mark.eval_live
@pytest.mark.skipif(not _has_keys(), reason="No API keys for judge validation")
class TestJudgeValidation:
    def test_validate_tpr_tnr(self, golden_set):
        """Skip if draft; validate TPR/TNR >= 0.80 if signed off."""
```

### `tests/eval/test_retrieval_quality.py` (NEW)

```python
@pytest.mark.eval_offline
class TestRecallAtK:
    def test_recall_perfect_when_all_relevant_retrieved(self):
        """Deterministic: known inputs → known Recall@K."""

    def test_recall_partial(self):
        """Some relevant candidates missed."""

    def test_recall_skipped_when_recall_off(self):
        """Skip with message when index.recall.enabled=false."""

@pytest.mark.eval_offline
class TestContextualPrecision:
    def test_precision_perfect_when_all_retrieved_relevant(self):
        ...

    def test_precision_degraded_with_irrelevant(self):
        ...

@pytest.mark.eval_live
@pytest.mark.skipif(not _has_keys(), reason="No API keys for DeepEval retrieval metrics")
class TestDeepEvalRetrievalMetrics:
    def test_contextual_recall_metric(self, golden_set):
        """Run ContextualRecallMetric over golden cases (signed-off only)."""

    def test_contextual_precision_metric(self, golden_set):
        """Run ContextualPrecisionMetric over golden cases (signed-off only)."""
```

---

## Golden set case design

The 20–40 cases span three axes:

### Axis 1: Role coverage (base cases from fixtures)

| Case ID | Role | Type | Tests |
|---------|------|------|-------|
| `GS-ROLE-01-shortlist` | ROLE-01 | shortlist (4 ranked) | faithfulness, recall, precision |
| `GS-ROLE-02-shortlist` | ROLE-02 | shortlist (3 ranked) | faithfulness, recall, precision |
| `GS-ROLE-03-no-match` | ROLE-03 | no-match | recall (near-miss relevant set) |

### Axis 2: Perturbation variants

For each base case, perturb one axis to test a boundary:

- **Availability shift:** move a candidate's date to ±1 day of the deadline.
- **Skill swap:** replace a hard skill with an adjacent skill.
- **Location change:** move a candidate to/from the role city.
- **Pool size:** add/remove candidates to create single-candidate or large-pool
  scenarios.
- **Scoring edge:** candidates with identical sub-scores (tie-breaking exercise).

### Axis 3: Faithfulness adversarials

Crafted cases where the narrative is **not** faithful:

- **Fabrication:** narrative claims a skill the candidate doesn't have.
- **Contradiction:** narrative says "strong Kotlin" but feedback says "struggled
  with Kotlin".
- **Score inconsistency:** narrative praises highly but sub-scores are low.
- **Evidence mismatch:** narrative cites evidence that doesn't exist in the
  candidate data.

These carry `faithfulness_labels: {cid: false}` so the judge validation has
true-negative cases.

---

## Fixture extensions

New fixture roles (ROLE-04 through ROLE-06) are needed for golden-set coverage.
They are added to `tests/fixtures/__init__.py` following the existing pattern
(`_candidate` helper + typed scorecard).

| Role | Purpose | Hard skills | Location | Notes |
|------|---------|-------------|----------|-------|
| ROLE-04 | Remote-India, hard skill java/spring boot/postgresql | java, spring boot, postgresql | Remote India | Tests multi-skill exact filter + remote gate |
| ROLE-05 | Single-candidate pool | python | Mumbai | Only one candidate passes all gates — tests ranking degenerate case |
| ROLE-06 | Tie-breaking | react, typescript | Chennai | Multiple candidates with identical skill sets — exercises deterministic tie-break |

---

## Test placement and marker scheme

All new tests live in `tests/eval/` under existing marker conventions (AD-096):

| File | Marker | Runs in `make check`? | Runs in `make eval`? |
|------|--------|-----------------------|----------------------|
| `test_retrieval_quality.py` (deterministic Recall@K tests) | `eval_offline` | **No** | Yes |
| `test_retrieval_quality.py` (DeepEval metrics) | `eval_live` | No | Yes (skipif no keys) |
| `test_faithfulness.py` | `eval_live` | No | Yes (skipif no keys) |

**Why not in `make check`?** Even the deterministic Recall@K tests need either
a populated index (setup cost) or synthetic retrieved-id lists (which are
already tested by unit tests of `compute_recall_at_k`). Placing them in
`make eval` keeps `make check` fast and focused on the commit gate.

---

## Out of scope

- **Modifying the six deterministic invariants** — they are the commit gate and
  stay untouched.
- **Building the golden set automatically from live data** — the labels are
  hand-authored (machine-drafted, human-signed-off).
- **Online monitoring / observability dashboards** — beyond POC.
- **Tuning retrieval parameters** (dense top-N, BM25 weights, RRF k) — this
  slice measures; tuning is a follow-on.
- **AD-084 embed-path PII scan** — separate slice (c-004 was re-sequenced;
  this spec is the eval layer, not the PII scan).
