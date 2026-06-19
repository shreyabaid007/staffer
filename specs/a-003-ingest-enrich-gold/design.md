# Design — a-003 Ingest Enrich, PII Boundary & Canonical-Entity Merge

> Technical design for Slice 3: silver (+ LLM enrich) → one canonical `GoldCandidate` per
> `candidate_id`, PII-clean and cited, plus reconcile + tombstones. References: `requirements.md`
> (this folder); `ee-ingestion-architecture.md` §5/§6(Phase 4/5)/§7/§9/§10/§11/§12/§13;
> AD-010/AD-011/AD-032/AD-033/AD-060/AD-062/AD-068/AD-069/AD-070/AD-073; AD-030/AD-031; `docs/tech.md`
> rules 1/2/4/5/6; `docs/structure.md` dep rules; precedents AD-075/AD-076 (a-002).

## Phase contracts (the two new stages)

| Step | Name | Input | Output | LLM? |
| --- | --- | --- | --- | --- |
| 4 | **Enrich** | `NormalizedRecord` (resume/feedback `raw_text`) | `ProfileSummaryExtraction` · `FeedbackExtraction` | **Yes** (PII-bracketed) |
| 5 | **Gold** | all silver + enriched for one `candidate_id` | `GoldCandidate` (cited, conflicts resolved) | No |

Reconcile + tombstones + freshness, gold persistence (+`gold_hash`), and the lineage-metrics
extension sit around step 5 (§5/§10/§12).

## Data flow (one run)

```
parse → silver (a-002) ─┬─ supply NormalizedRecords ───────────────┐
                        ├─ resume  NormalizedRecord.raw_text ─┐     │
                        └─ feedback NormalizedRecord.raw_text ┤     │
                                                              ▼     ▼
                  enrich:  anonymize → leak-scan(GATE) → LLM(temp0,DSPy) → de-anon → verify-quote
                                                              │
                            ProfileSummaryExtraction / FeedbackExtraction (cited)
                                                              ▼
            merge:  group by candidate_id → authority rules (§7) → conflicts → MergedSkill
                    → carry cited feedback facts → GoldCandidate (+gold_hash)
                                                              ▼
            reconcile: current vs prior candidate_id set → tombstone departed; freshness guard
                                                              ▼
            goldstore: write gold/<candidate_id>.json (atomic, gitignored)
                                                              ▼
            lineage: conflict rate · leak-scan hits · citation-verify failures · tombstones · coverage
```

## Modules touched

| Module | New/Edit | Responsibility | Owner |
| --- | --- | --- | --- |
| `dsm/pii/redact.py` | **new** | generic deterministic redact (known PII first) + Presidio NER residual + `deanonymize` | Lane C dir (Lane A seeds; AD-078) |
| `dsm/pii/leakscan.py` | **new** | generic outbound leak-scan hard gate (`leak_scan`/`assert_no_leak`/`PIILeakError`) | Lane C dir (Lane A seeds; AD-078) |
| `dsm/pii/pseudonymised_lm.py` | (reuse) | the only authorised LLM path (already exists; configured at temp 0) | Lane C |
| `dsm/ingest/enrich.py` | **new** | DSPy signatures + `enrich_resume`/`enrich_feedback`: anonymize→gate→LLM→deanon→verify | Lane A |
| `dsm/ingest/merge.py` | **new** | silver+enriched → `GoldCandidate`; §7 authority; conflict recording; carry feedback facts | Lane A |
| `dsm/ingest/reconcile.py` | **new** | snapshot diff · tombstones · latest-wins · freshness guard | Lane A |
| `dsm/ingest/goldstore.py` | **new** | `gold_hash` + atomic `write_gold`/`read_gold`/`list_gold_ids` | Lane A |
| `dsm/ingest/models.py` | **edit** | add `Sourced[T]`, `SkillExtraction`, `ProfileSummaryExtraction`, `FeedbackExtraction`, `MergedSkill`, `GoldCandidate` | Lane A |
| `dsm/ingest/lineage.py` | **edit** | quality metrics (conflict rate, leak hits, citation-verify failures, tombstones, coverage) | Lane A |
| `dsm/models.py` | **edit** | relax `EvidenceCitation` (add optional `source_hash`/`locator`) — AD-077 | Lane C dir (sign-off) |
| `dsm/cli/commands.py` | **edit** | wire enrich→merge→reconcile→goldstore into `dsm ingest`; PII-safe gold summary | Lane C dir (precedent a-001/a-002) |
| `config/default.yaml` | **edit** | `enrich:` (prompt/model versions), `reconcile:` staleness | config |
| `config/prompts/` | **new** | versioned DSPy signature instructions (`profile_extraction`, `feedback_extraction`) | Lane A |
| `pyproject.toml` | **edit** | relax NF-3 import contract (ingest → pii boundary + dspy) — AD-078 | import contract |
| `.gitignore` | **edit** | ignore `gold/*` (GS-3, PII-adjacent) | — |

`Grade`/`Confidence`/`NormalizedSkill`/`NormalizedRecord` (a-002) and frozen
`Location`/`AvailabilityState`/`ProficiencyLevel` are reused, not redefined.

## Naming & frozen-contract collisions (decided by the slice brief — recorded, not re-litigated)

The architecture §6 names two Phase-5 types that **collide with frozen `dsm/models.py`** names. To
respect AD-060 (no silent frozen divergence) we keep them **ingest-local with non-colliding names**:

| §6 name | Collides with frozen | Resolution (ingest-local) |
| --- | --- | --- |
| `Candidate` (enriched gold) | `dsm.models.Candidate` (serving) | **`GoldCandidate`** — distinct concept (sourced, conflict-aware, vault-refs). Bridging gold→serving `Candidate` happens at the index/serving boundary (later slice). |
| `FeedbackSignals` (per-item LLM out) | `dsm.models.FeedbackSignals` (aggregate) | **`FeedbackExtraction`** — per feedback item; gold holds `list[FeedbackExtraction]`. |
| `EvidenceCitation` | `dsm.models.EvidenceCitation` | **Reuse the frozen one**, relaxed (AD-077) — one citation type system-wide (per brief). |

`Sourced[T]`, `SkillExtraction`, `ProfileSummaryExtraction`, `MergedSkill` have no frozen collision →
new ingest-local types.

## Data contracts

### Frozen edit — `EvidenceCitation` (AD-077, the one sanctioned frozen change)
```python
class EvidenceCitation(BaseModel, frozen=True):
    source: EvidenceSource                       # unchanged
    text: str                                    # AD-073 VERIFIED verbatim quote (semantics tightened)
    source_hash: str | None = None               # NEW — which bronze blob (lineage to source)
    locator: str | None = None                   # NEW — "resume p1 SKILLS" | "feedback fb_0"
    metadata: dict[str, str] = Field(default_factory=dict)   # unchanged
```
Backwards-compatible: existing constructions (e.g. gates/score tests) keep working — new fields are
optional. `make check` (pyright + existing tests) must stay green after the edit.

### Ingest-local (new in `dsm/ingest/models.py`)
```python
from typing import Generic, Literal, TypeVar
from dsm.models import (AvailabilityState, EvidenceCitation, Location, ProficiencyLevel)
# Grade, Confidence, NormalizedSkill already ingest-local (a-002)

T = TypeVar("T")

class Sourced(BaseModel, Generic[T], frozen=True):
    value: T
    citations: list[EvidenceCitation] = Field(default_factory=list)
    confidence: Confidence = Confidence.MEDIUM

# --- Phase 4: LLM extraction outputs (DSPy signature return types) ---
class SkillExtraction(BaseModel, frozen=True):
    name: str                                    # surface form, pre-normalization
    proficiency: ProficiencyLevel | None = None
    evidence: EvidenceCitation

class ProfileSummaryExtraction(BaseModel, frozen=True):     # resume
    skills: list[SkillExtraction] = Field(default_factory=list)
    employers: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    seniority_signals: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)

class FeedbackExtraction(BaseModel, frozen=True):           # per feedback item (renamed; §6)
    confirmed_skills: list[str] = Field(default_factory=list)
    skill_gaps: list[str] = Field(default_factory=list)
    domain_confirmation: str | None = None
    sentiment: Literal["very_positive", "positive", "neutral", "negative"]
    retention_requested: bool = False            # PRD §3.4 +0.15
    rejection_requested: bool = False            # PRD §3.4 −0.25, never decayed
    summary: str
    evidence: EvidenceCitation

# --- Phase 5: canonical gold ---
class MergedSkill(BaseModel, frozen=True):
    name: str                                    # canonical taxonomy id
    proficiency: ProficiencyLevel | None = None  # resume > CSV
    demonstrated: bool | None = None             # feedback > resume; None = unverified
    unverified: bool = False                     # AD-032 new-joiner provenance, carried from silver
    confidence: Confidence
    citations: list[EvidenceCitation] = Field(default_factory=list)
    conflict: str | None = None                  # set on resume↔feedback disagreement (MG-5)

class GoldCandidate(BaseModel, frozen=True):     # ≈ §6 Candidate, renamed
    candidate_id: str
    name_vault_ref: str                          # vault pointer, never raw name (GS-4)
    email_vault_ref: str
    grade: Sourced[Grade] | None = None          # optional → partial tolerance (PP-1)
    location: Sourced[Location] | None = None
    availability: Sourced[AvailabilityState] | None = None
    skills: list[MergedSkill] = Field(default_factory=list)
    domains: list[Sourced[str]] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    feedback: list[FeedbackExtraction] = Field(default_factory=list)   # cited facts; SCORE computed at match time (Lane B), not here (FB-1/FB-2, AD-079)
    valid_as_of: date | None = None
    is_tombstoned: bool = False
    conflicts: list[str] = Field(default_factory=list)
    gold_hash: str                               # change-detection for re-index (GS-2)
    merge_version: str
    prompt_version: str
    model_version: str
```
> A `GoldCandidate` requires **at least one valid supply-derived silver record** (the candidate
> universe is the supply sheets, AD-013). A `candidate_id` whose every supply record was
> coercion-skipped (a-002 AV-4) has no supply state → logged+skipped+counted, no gold entity.

## PII boundary design (`dsm/pii/`, generic + reusable)

The brief's hard constraint: keep the surface free of ingest-only assumptions so match/clarify and
match/score reuse it later **without importing `dsm.ingest`**. Hence the inputs are plain
`text` + `known_pii: list[str]`, not ingest models.

### `redact.py`
```python
class RedactionResult(BaseModel):
    text: str                          # redacted, safe to leak-scan then send
    mapping: dict[str, str]            # placeholder -> original; IN-MEMORY ONLY, never persisted/logged

def redact(text: str, *, known_pii: list[str]) -> RedactionResult: ...
def deanonymize(text: str, mapping: dict[str, str]) -> str: ...
```
- **Order (AD-069):** (1) deterministically replace each `known_pii` string (the supply-row name +
  email) with a stable placeholder (`[[PII_0]]`, …) — exact + case-insensitive whole-token match; (2)
  run Presidio NER over the residual for residual `PERSON` + `ORG` spans and tokenize those too.
- Placeholders are stable within a single call so de-anonymization is exact. The mapping is returned,
  used to de-anonymize the structured output, then dropped (PII-3).
- **Lane split:** Lane A seeds a working redactor (deterministic pass guaranteed correct; a thin
  Presidio NER pass with a documented seam). Lane C later hardens NER (Indian-surname tuning, the
  client-org dictionary, §15#4). The **deterministic known-PII pass is the load-bearing guarantee**;
  the leak-scan backstops NER imperfection.

### `leakscan.py` — hard gate
```python
class PIILeakError(RuntimeError): ...
class LeakScanResult(BaseModel):
    clean: bool
    hits: list[str]                    # which known-PII strings were found (logged as a count, not value)

def leak_scan(text: str, *, known_pii: list[str]) -> LeakScanResult: ...
def assert_no_leak(text: str, *, known_pii: list[str]) -> None:      # raises PIILeakError on any hit
```
- The single outbound choke point: `enrich` calls `assert_no_leak(redacted_text, known_pii=...)`
  immediately before the LLM call. A hit raises → the call never happens → the run/eval fails (PII-5,
  LN-4). The eval invariant `no-PII-leak` asserts zero hits over the suite.

### `pseudonymised_lm.py`
Reused as the LLM path. Configured with `temperature=0` and the pinned reasoning model. (Its internal
anonymise/deanonymise hooks remain the live-call boundary for match/clarify/score; ingest's enrich
does its **own** redact+leakscan around the call as the architecture specifies, because ingest knows
the known-PII set from the supply row — defense in depth, both layers active.)

## Enrich design (`dsm/ingest/enrich.py`, Lane A)

DSPy typed `Signature`s — **no raw prompt strings** (tech.md):
```python
class ProfileExtraction(dspy.Signature):
    """Extract structured profile facts from an anonymized resume. Quote verbatim spans as evidence."""
    resume_text: str = dspy.InputField()
    sections: list[str] = dspy.InputField()
    extraction: ProfileSummaryExtraction = dspy.OutputField()

class FeedbackExtractionSig(dspy.Signature):
    """Extract structured signals from one anonymized feedback item. Quote verbatim spans as evidence."""
    feedback_text: str = dspy.InputField()
    extraction: FeedbackExtraction = dspy.OutputField()
```
Entry points (pure orchestration; the LLM is the only side effect, mockable for cassettes):
```python
def enrich_resume(record: NormalizedRecord, *, known_pii: list[str], lm, run_id: str,
                  prompt_version: str, model_version: str) -> ProfileSummaryExtraction | None
def enrich_feedback(record: NormalizedRecord, *, known_pii: list[str], lm, run_id: str, ...) \
                  -> FeedbackExtraction | None
```
Each: `redact` → `assert_no_leak` (gate) → `dspy.Predict(<Sig>)(...)` via `PseudonymisedLM` at temp 0 →
de-anonymize the structured output → **verify every `EvidenceCitation.text` exists verbatim in the
original `raw_text`** (EN-4); drop+log+count any fact whose quote is absent; return `None` (log+count)
on a schema-invalid LLM response (EN-7). `known_pii` is built by the caller from the vault/supply row
(name+email) — `enrich` itself never sees the raw email beyond the redact input.

**Citation-verify (AD-073).** Verify against the **original** (de-anonymized) source text, not the
anonymized text, so a quote containing a (restored) name/org is checked against reality. Verbatim =
exact substring after whitespace normalization (documented, deterministic). A failed fact is rejected
individually; the rest of the extraction stands.

**Determinism/versioning (EN-6/NF-1/NF-2).** `prompt_version` + `model_version` come from `config/`
(`enrich.prompt_version`, `models.reasoning_llm` pinned id). **No cache layer this slice** (deferred,
§11). Determinism is therefore bounded at the LLM step: every non-LLM stage is byte-identical by
construction; the LLM is pinned **only in tests, by cassettes** (the byte-identical guarantee NF-1
asserts). A **live** run is *not* byte-identical — `temp=0` only reduces drift; the version stamps
buy "no *silent* drift" (a model/prompt change is a visible bump that forces re-extraction), not
token-identical replay. Hardening live reproducibility is out of scope here.

## Merge design (`dsm/ingest/merge.py`, Lane A)

```python
def merge_candidate(candidate_id: str, silver: list[NormalizedRecord],
                    profile: ProfileSummaryExtraction | None,
                    feedbacks: list[FeedbackExtraction],
                    name_ref: str, email_ref: str, *, merge_version: str,
                    prompt_version: str, model_version: str) -> GoldCandidate
def merge_run(...) -> list[GoldCandidate]    # group by candidate_id, deterministic order
```
Authority (§7 table), implemented as pure functions:
- **grade/location/availability** ← latest supply snapshot (max `valid_as_of`), each → `Sourced` with
  the supply citation (`source=SUPPLY_SHEET`).
- **skill names** ← union over {supply, resume.skills, feedback.confirmed_skills}, taxonomy-canonical.
- **proficiency** ← resume value if present else None (CSV has none).
- **demonstrated** ← feedback wins: in `confirmed_skills` → True; in `skill_gaps` (denied) → False;
  neither → None. New-joiner-origin skills keep `unverified=True` (MG-7).
- **conflict (MG-5)** ← resume asserts skill X and feedback denies X → `demonstrated=False`,
  `conflict="resume claims <X>; feedback denies <X>"`, **both** citations on the `MergedSkill`, and a
  line appended to `GoldCandidate.conflicts`. **Never averaged.**
- **domains** ← resume claims as `Sourced[str]`; feedback `domain_confirmation` match → bump
  `confidence` (medium→high), not overwrite. **projects** ← resume only.
- **feedback** ← `list[FeedbackExtraction]` carried verbatim (the cited facts, FB-1). **No
  feedback/track-record score is computed at ingest** (FB-2; Sign-off item 3 / AD-079) — that is a
  match/score concern (Lane B). Determinism: all list builds sorted (MG-8); `merge_version` stamped (MG-9).

## Feedback score — deferred to match/score (NOT this slice; AD-079)

Architecture §6 placed `performance_feedback_score` on the gold entity; this slice **moves it to
match/score** and gold carries only the cited `FeedbackExtraction` facts. Why (see Sign-off item 3):
the frozen `CandidateAssessment` already owns `feedback_score`/`combined_score`; score computation is
the match Python step (AD-030, tech.md rule 4, Lane B per AD-062); and the unconfirmed §3.4 decay
curve must not be frozen into the immutable gold layer (a constant tweak would force a full re-merge).
Lane B will derive the feedback term from `GoldCandidate.feedback` at query time. **No
`score_feedback.py`, no `feedback_score:` config block in this slice.**

## Reconcile design (`dsm/ingest/reconcile.py`, Lane A)
```python
class ReconcileResult(BaseModel):
    tombstoned_ids: list[str]
    freshness_warnings: list[str]

def reconcile(current_ids: set[str], prior_ids: set[str]) -> ReconcileResult   # tombstoned = prior - current
def freshness_guard(valid_as_of: date | None, *, max_staleness_days: int, today: date) -> list[str]
```
- **Tombstones (RC-1/RC-4):** ids in prior `gold/` not in this run → the merge sets
  `is_tombstoned=True` on a carried-forward `GoldCandidate` (read prior, flip flag, re-persist). Gold
  is retained (audit). Counted in metrics.
- **Latest-wins (RC-2):** already enforced in merge by max `valid_as_of`; reconcile asserts it across
  the run.
- **Freshness (RC-3):** warn when `today − valid_as_of > max_staleness_days` (config). The refuse-vs-
  role-start half is a **match-time** decision (referenced, not built). `today` injected (RC-5).

## Gold storage (`dsm/ingest/goldstore.py`, Lane A)
```python
def gold_hash(candidate: GoldCandidate) -> str    # sha256 over model_dump(exclude={"gold_hash"}), canonical/sorted
def write_gold(candidate: GoldCandidate, gold_root: Path) -> Path   # gold/<cid>.json, atomic temp+rename
def read_gold(candidate_id: str, gold_root: Path) -> GoldCandidate | None
def list_gold_ids(gold_root: Path) -> set[str]
```
- `candidate_id` is `cid:<hex>`; the filename strips the `cid:` prefix (mirrors silver's `sha256:`
  handling). Atomic write mirrors `blobstore.write_records`/`silver.write_normalized` (GS-1).
- `gold_hash` excludes itself, sorts keys → stable; the **index** phase compares it to re-embed only
  changed entities (GS-2; consuming it is out of scope here).
- `.gitignore`: add `gold/*` (+ `.gitkeep`) — gold carries vault refs + verbatim quotes that can
  include client orgs (GS-3).

## CLI wiring (`dsm/cli/commands.py`, Lane C dir)
After the existing parse+silver loop, `dsm ingest`:
1. splits silver into supply / resume / feedback;
2. runs `enrich` over resume+feedback records (real `PseudonymisedLM` at temp 0; **cassettes in
   tests**), building `known_pii` per candidate from the vault;
3. `merge_run` → `GoldCandidate`s; `reconcile` vs `list_gold_ids(gold_root)`; `write_gold` each;
4. prints a **PII-safe `── Gold ──` summary**: gold entities written, coverage split
   (thin/medium/rich), conflicts recorded, citation-verify failures, leak-scan hits (**must be 0**),
   tombstones, freshness warnings. **No `raw_text`/name/email/quote bodies to stdout** — per-entity
   line is `cid=<id> grade=<g> avail=<t> skills=<n> conflicts=<n>` only.
- **Exit semantics:** expected invalid data (skipped extractions, coercion skips) is counted, not a
  failure; a **leak-scan hit** or an unexpected exception exits non-zero (PII-5/LN-4). Fail fast if the
  reasoning-LLM/OpenRouter credentials are unset when a live enrich is requested (tests use cassettes,
  no creds needed). `DSM_CANDIDATE_ID_KEY` still required (a-002).

## lineage extension (`dsm/ingest/lineage.py`, §12)
Add: `log_leak_block`, `log_citation_verify_failure`, `log_conflict`, `log_tombstone`; and
stream-derived counters `count_conflicts`, `count_citation_failures`, `coverage(gold)` (thin/medium/
rich), `count_tombstones`. Deterministic (derive from the gold/event stream, LN-3). Reuse a-002's
`log_invalid`/`log_unmapped_skill` patterns.

## Import contract relax (`pyproject.toml`, NF-3 / AD-078)
- Edit the "Ingest must not depend on match, index, or LLM code" contract: **remove `dspy`** from its
  forbidden list (keep `dsm.match`, `dsm.index`, `modal`, `httpx`).
- Edit the "Ingest may use only dsm.pii.vault" contract: it currently forbids
  `dsm.pii.pseudonymised_lm`/`dsm.pii.stub`. **Drop `dsm.pii.pseudonymised_lm`** from the forbidden
  list so ingest can reach the LLM path; `redact`/`leakscan` are permitted by being absent from any
  forbidden list. (Net: ingest may import all of `dsm.pii`; `modal`/`httpx`/`dsm.match`/`dsm.index`
  stay forbidden — no direct provider access, only *through* `PseudonymisedLM`.) Own commit, note → AD-078.

## Dependencies, config
- **New runtime dep risk:** **Presidio + spaCy `en_core_web_lg`** (tech.md §PII already lists them; not
  yet in `pyproject`). Adding them is in `docs/tech.md`, so **no new ADR** is needed, but it **is** a
  `pyproject`/lockfile change — call it out at sign-off. The redactor's deterministic known-PII pass
  works without Presidio; the NER pass is behind a mockable seam so `make check` stays offline (mirrors
  a-001's Docling `_extract` seam). **DSPy** is already a dependency (used by `pseudonymised_lm.py`).
- `config/default.yaml`: add `enrich.prompt_version`, reuse `models.reasoning_llm` as `model_version`;
  `reconcile.max_staleness_days`. _(No `feedback_score:` block — the feedback score is Lane B's, AD-079.)_
- `config/prompts/`: versioned signature instruction text (so a prompt change is a visible version bump).

## Eval / unit cases to add (deterministic — recorded LLM cassettes, no live calls)

| # | Case | Fixture | Criterion |
| --- | --- | --- | --- |
| 1 | redact removes known name+email | resume text + supply name/email | PII-1 (placeholders present, originals gone) |
| 2 | NER residual name/org tokenized | text w/ an unlisted name + client org | PII-2 (seam mockable) |
| 3 | **leak-scan blocks residual PII + FAILS build** | text still containing a known string | PII-5/LN-4 (`PIILeakError`, non-zero) |
| 4 | mapping never persisted/logged | any redact | PII-3 |
| 5 | resume → ProfileSummaryExtraction (cassette) | recorded LLM response | EN-1 |
| 6 | feedback → FeedbackExtraction (cassette) | recorded LLM response | EN-2 |
| 7 | **citation-verify rejects a fabricated/absent quote** | cassette w/ a quote not in source | EN-4 (fact dropped, logged, counted; siblings kept) |
| 8 | schema-invalid LLM output → skip+count, no crash | malformed cassette | EN-7 |
| 9 | **§7 worked example** resume IaC vs feedback denies Terraform | resume+feedback cassettes | MG-5 (`demonstrated=false`, both citations, conflict recorded, **not averaged**) |
| 10 | proficiency resume>CSV; names unioned | supply+resume | MG-3 |
| 11 | demonstrated feedback>resume; None when silent | rich/medium | MG-4 |
| 12 | new-joiner skills stay `unverified` to gold | new-joiner + resume | MG-7 (AD-032) |
| 13 | feedback **facts** carried to gold (no score) | rich profile w/ retention + rejection | FB-1/FB-2 (`FeedbackExtraction` list w/ citations; **no score field on gold**) |
| 14 | empty feedback list on thin/medium | thin/medium | FB-1 (empty list, valid gold) |
| 15 | **tombstone when candidate_id disappears** | prior gold has id X; current run lacks X | RC-1 (X `is_tombstoned=true`, retained, counted) |
| 16 | latest-snapshot-wins across sheet move | two snapshots, moved sheet | RC-2 |
| 17 | freshness guard warns on stale snapshot | old `valid_as_of` + injected today | RC-3 |
| 18 | **thin/medium/rich each yield a valid GoldCandidate** | three fixtures | PP-1/PP-2/PP-3 |
| 19 | gold round-trip + `gold_hash` stable & change-sensitive | write→read; mutate→hash differs | GS-1/GS-2 |
| 20 | gold has vault refs, no raw name/email | any gold | GS-4 |
| 21 | determinism end-to-end | same inputs+versions twice | NF-1 (byte-identical) |
| 22 | model_version bump forces re-extract | bump version | NF-2 |
| 23 | CLI `── Gold ──` summary PII-safe | run ingest over fixture raw dir (cassettes) | CLI (no raw_text/name/email/quote in stdout; leak hits=0) |
| 24 | import contract green | — | NF-3 (ingest→pii+dspy allowed; modal/httpx/match/index forbidden) |

Fixtures under `tests/fixtures/ingest/enrich/` (synthetic) + recorded cassettes under
`tests/fixtures/ingest/cassettes/`. Fixed `DSM_CANDIDATE_ID_KEY` in the harness.

These map to the **eval invariants** (`make eval`, when wired): `no-PII-leak` (cases 3/23),
`evidence-cited` (cases 5–7), `gates-respected`/`hard-skill-not-cleared-by-adjacency` (case 9 feeds
them downstream), `determinism` (cases 21/22).

## Edge cases
- A candidate whose every supply record was coercion-skipped (a-002) → no supply state → log+skip, no
  gold (AD-013).
- Resume/feedback with an email that maps to a `candidate_id` having **no** supply row → not a
  candidate (AD-013); the enrichment is logged+counted and not merged.
- Feedback denies a skill the resume never claimed → `demonstrated=false` with the feedback citation,
  no conflict (nothing to disagree with).
- Quote that exists in the source only after the redaction placeholder is restored → verify **after**
  de-anonymization (handled by EN-4 ordering).
- LLM returns a citation whose quote spans a client-org token that NER missed → leak-scan already
  blocked the call upstream; can't reach here.
- Empty resume sections / empty feedback summary → valid extraction with empty lists; merge tolerates.

## Superseding ADRs to add (in `docs/decision.md`, T-000-ADR)

> **AD-077 · Relax `EvidenceCitation` (add optional source_hash/locator; text = verified quote).**
> *Status: Proposed → Accept at sign-off.* Adds `source_hash: str | None = None` and
> `locator: str | None = None` to frozen `dsm/models.py::EvidenceCitation`; tightens `text` to mean the
> AD-073 **verified verbatim quote**. Backwards-compatible (new fields optional). Supersedes AD-060's
> `EvidenceCitation` shape. Why: gold/enrich need lineage-to-source on every citation, and the brief
> mandates **one citation type system-wide** (ingest + serving) rather than a parallel ingest type.
> Consequence: gates/score/rank tests unaffected; Lanes B/C re-pull. Verify `make check` green.

> **AD-078 · Relax the ingest import boundary to the PII layer + DSPy (the sanctioned LLM path).**
> *Status: Proposed → Accept at sign-off; **requires Lane C agreement**.* `dsm.ingest` MAY import
> `dsm.pii.{pseudonymised_lm,redact,leakscan,vault}` and `dspy`; it remains forbidden from
> `dsm.match`, `dsm.index`, `modal`, `httpx` (the external provider is reached **only through**
> `PseudonymisedLM`). New **generic** `dsm/pii/redact.py` + `dsm/pii/leakscan.py` (Lane C dir per
> AD-062) are **seeded by Lane A** this slice and **hardened by Lane C** later (Presidio NER tuning,
> org dictionary) — Lane C agrees to placement + seeding (mirrors AD-076). Why: enrich is the first LLM
> call site and must route through the PII boundary; this **routes through** the boundary (does not
> weaken it) and makes the outbound leak-scan a hard gate. Consequence: a deliberate, signed-off
> widening of the ingest import surface to the sanctioned path only; tech.md rule 1 upheld.

> **AD-079 · Feedback *facts* in gold; feedback *score* in match/score (supersedes §6 field placement).**
> *Status: Proposed → Confirm divergence at sign-off.* The gold entity carries the cited
> `list[FeedbackExtraction]` (facts) but **not** a `performance_feedback_score` — that field is dropped
> from the gold contract this slice. The feedback/track-record score and the `0.7·skill+0.3·feedback`
> combine are computed at **match/score** time (Lane B) into the frozen `CandidateAssessment.feedback_score`
> / `combined_score` (AD-030, tech.md rule 4, AD-062). Supersedes architecture §6's placement of
> `performance_feedback_score` on the gold `Candidate`. Why: (i) redundant with the frozen serving
> contract; (ii) scoring is the match lane's job, not ingest's; (iii) the unconfirmed PRD §3.4 decay
> curve must not be frozen into the immutable gold layer (a constant tweak would force a full re-merge —
> in match/score it is a `config/` change with no re-ingest). Consequence: the §3.4 formula + whether
> feedback items carry dates are deferred to the Lane B scoring spec, not settled here.
