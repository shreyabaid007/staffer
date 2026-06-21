# Design — a-005 Index BUILD + UPSERT

> Technical design for projecting `GoldCandidate` → `CandidateIndexRecord`, embedding the capability
> passage via the a-004 `EmbedClient`, and upserting into Milvus Lite. References: `requirements.md`
> (this folder); `ee-ingestion-architecture.md` §6/§8; AD-066/AD-072/AD-074; the merged
> `dsm/index/embed_client.py`; `docs/tech.md` rules 1/2/5/6.

## Architecture

```
GoldCandidate (gold/<cid>.json)            dsm/index/  (this slice)
  read via dsm.ingest.goldstore     ┌─────────────────────────────────────────────┐
        │                           │ text_builder.py  build_embed_text/skill_set  │  pure (PII-free
        ▼                           │ models.py        CandidateIndexRecord + proj  │  by construction)
   indexer.index_gold ──────────────┤ indexer.py       orchestrate + metrics        │
        │   │                        │ milvus_store.py  ensure/upsert/delete/versions│ pymilvus
        │   └──── embed(passage) ────┤ (dsm.index.embed_client.EmbedClient)          │──▶ Modal (prod)
        ▼                            └─────────────────────────────────────────────┘     Fake (tests)
   Milvus Lite collection "candidates"  (data/index/milvus.db, gitignored)
```

Boundaries (all permitted under the a-004-settled contracts — **no new contract**): `dsm.index` →
`dsm.ingest.goldstore`/`dsm.ingest.models` (read gold + `Grade`), `dsm.models`
(`Location`/`AvailabilityState`), `pymilvus`/`milvus_lite`, `modal` (via the existing client).
`gates ⊥ index` and `ingest ⊥ index` are untouched. (No `dsm.pii` import this slice — the outbound
known-PII scan is dropped per AD-084; PII safety is by construction, see below.)

## `CandidateIndexRecord` (`dsm/index/models.py`)

The §6 Phase 6 model, plus `model_version` (AD-082). `frozen=True`, Pydantic v2.

```python
from datetime import date
from typing import Literal
from dsm.ingest.models import Grade          # ingest-local enum
# Location / AvailabilityState come from dsm.models (frozen)

class CandidateIndexRecord(BaseModel, frozen=True):
    candidate_id: str
    embed_text: str                 # capability-only, PII-free (the embedded text)
    dense_vector: list[float]       # 768-dim, L2-normalized (from EmbedClient.embed)
    skill_set: list[str]            # EXCLUDES demonstrated-False skills (AD-081)
    grade: Grade
    city: str | None
    remote_eligible: bool
    availability_type: Literal["free_now", "rolling_off", "new_joiner"]
    availability_date: date | None
    valid_as_of: date | None
    gold_hash: str
    model_version: str              # = config models.embedder; re-embed on change (AD-082)
```

### Projection helpers (pure)

- **`is_indexable(gold) -> bool`** — `gold.grade is not None and gold.location is not None and
  gold.availability is not None`. A `False` here is the IDX-8 thin-skip (the record's `grade` /
  `availability_type` are non-optional; we refuse to guess them). `gold.is_tombstoned` is handled
  *before* this check (delete path).
- **`project_filter_fields(gold) -> dict`** — from the `Sourced[...].value`s:
  - `grade = gold.grade.value`
  - `loc = gold.location.value` → `city = loc.city`, `remote_eligible = loc.remote_eligible`
  - `avail = gold.availability.value` (discriminated union):
    - `FreeNow` → `("free_now", None)`
    - `RollingOff` → `("rolling_off", avail.expected_date)`
    - `NewJoiner` → `("new_joiner", avail.join_date)`
  - `valid_as_of = gold.valid_as_of`, `gold_hash = gold.gold_hash`
- **`build_record(gold, *, embed_text, dense_vector, skill_set, model_version)`** — assemble the
  frozen record from the above + the embedded inputs.

`Grade` is imported from `dsm.ingest.models` (not redefined). `model_version` is **the embedder id**,
distinct from `GoldCandidate.model_version` (which is the *reasoning* LLM used at enrich) — these mean
different things; the index gates on the embedder.

## `embed_text` + `skill_set` (`dsm/index/text_builder.py`, pure)

### Included-skills filter (single rule, AD-081 + AD-072)

```python
def included_skills(gold) -> list[MergedSkill]:
    # demonstrated True/None kept; False (feedback-refuted / negated) excluded
    return [s for s in gold.skills if s.demonstrated is not False]
```

Both builders consume this — `embed_text` excludes negations (AD-072) and `skill_set` excludes
refuted skills (AD-081) by the **same** predicate, so they never disagree.

### `build_skill_set(gold) -> list[str]`

`sorted({s.name for s in included_skills(gold)})` — deduped, sorted (determinism). Drives the
`skill_set` ARRAY field and the `skill_text` BM25 input.

### `build_embed_text(gold) -> str`

Deterministic composition, all parts sorted; **no** name/email/`candidate_id`/grade-label/availability:

1. **Contextual prefix** (Contextual-Retrieval one-liner): `"Domains: {', '.join(sorted(domain
   values))}."` when `gold.domains` is non-empty, else omitted. Domain values are
   `[d.value for d in gold.domains]`.
2. **Skill phrases**: for each included skill, sorted by name → `f"{name} {proficiency.value}"` when
   proficiency is set, else `name`; joined `", "` + `"."`.
3. **Project descriptions**: `sorted(gold.projects)` joined by `" "` — these carry the seniority
   *evidence* (led delivery, scale, years), which is why grade-as-a-label is not embedded.

`embed_text = " ".join(part for part in [prefix, skills_sentence, projects_text] if part)`.

> The exact prose is tunable; the invariants the tests pin are: PII-free, denied-skill-free,
> byte-identical for identical gold, and order-insensitive to the input skill list (sorted).

### PII safety — by construction (AD-084)

`build_embed_text`/`build_skill_set` read **only** `gold.skills`, `gold.domains[].value`, and
`gold.projects`. They never touch `name_vault_ref`/`email_vault_ref`/`candidate_id` or any identity
field, so identity cannot enter the passage bound for Modal. **No outbound known-PII scan runs at
index time** — the brief's `assert_no_leak` is dropped (a known-string scan needs per-candidate
name/email, which gold doesn't carry and which would couple `dsm index` to bronze, for only the
narrow de-anon-into-`projects` backstop). A **generic** NER/org-dictionary outbound scan (catches
client-org names without per-candidate identity) is **deferred to a later hardening phase**; `build_embed_text`
is the single seam where it would attach. A test pins the construction guarantee (AC-6): build gold
with vault refs populated → neither ref nor any identity appears in `embed_text`/`skill_set`.

## Milvus store (`dsm/index/milvus_store.py`)

Wraps `pymilvus.MilvusClient` against the local `milvus-lite` engine. **Verified offline** that Milvus
Lite supports the BM25 `Function` + sparse vector + `enable_analyzer` (probe in spec research).

### Schema (`ensure_collection`)

| Field | Type | Notes |
|---|---|---|
| `candidate_id` | `VARCHAR` (max 128) | primary key, `auto_id=False` |
| `dense` | `FLOAT_VECTOR(768)` | metric **IP**, AUTOINDEX |
| `skill_set` | `ARRAY<VARCHAR>` | exact hard-skill filter (`ARRAY_CONTAINS`, a-006) |
| `skill_text` | `VARCHAR`, `enable_analyzer=True` | BM25 input (space-joined skill_set) |
| `sparse` | `SPARSE_FLOAT_VECTOR` | BM25 output via `Function`; AUTOINDEX metric BM25 |
| `embed_text` | `VARCHAR` (large) | the embedded passage (stored for audit) |
| `grade` | `VARCHAR` | filter |
| `city` | `VARCHAR`, nullable | filter (`None` for Remote-India) |
| `remote_eligible` | `BOOL` | filter |
| `availability_type` | `VARCHAR` | filter |
| `availability_date` | `INT64`, nullable | `date.toordinal()`; `None` for free_now — range-filterable |
| `valid_as_of` | `INT64`, nullable | `date.toordinal()` — range-filterable |
| `gold_hash` | `VARCHAR` | change-detection (AD-082) |
| `model_version` | `VARCHAR` | embedder id (AD-082) |

Dates are stored as **INT64 proleptic-Gregorian ordinals** (`date.toordinal()`), not ISO strings, so
a-006 can express correct numeric range filters (`availability_date <= <deadline_ordinal>`) without a
re-index. The `CandidateIndexRecord` contract keeps `date | None`; the ordinal is a storage encoding
internal to `milvus_store` (decode with `date.fromordinal`).

`BM25 Function(input=skill_text, output=sparse)` — Milvus auto-computes `sparse` from `skill_text` at
insert; the writer never supplies `sparse`. `ensure_collection` is idempotent: create only if absent
(`client.has_collection`), then `load`.

### Operations

- **`MilvusIndexStore(db_path, collection="candidates", dim=768, metric="IP")`** — opens the client;
  `ensure_collection()` builds the schema above.
- **`upsert(records: list[CandidateIndexRecord]) -> None`** — maps each record to a row (`dense` =
  `dense_vector`, `skill_text` = `" ".join(skill_set)`, dates → ISO/None), `client.upsert(...)`.
  Idempotent: upsert replaces by PK, so re-running with identical data leaves one entity unchanged.
- **`delete(candidate_ids: list[str]) -> None`** — `client.delete(ids=...)`; no-op if absent
  (tombstones, IDX-7).
- **`fetch_versions(candidate_ids) -> dict[str, tuple[str, str]]`** — `client.query`/`get` returning
  `{candidate_id: (gold_hash, model_version)}` for the gating check (IDX-6). Missing ids absent from
  the map (→ treated as "needs embed").

Construction params (`db_path`, `collection`, `dim`, `metric`) are passed in by the CLI from the
`index` config block — the store reads no config itself (mirrors rank being config-free, AD-064).

## Indexer (`dsm/index/indexer.py`)

```python
def index_gold(
    candidate_ids: Iterable[str],
    *,
    read_gold: Callable[[str], GoldCandidate | None],   # goldstore.read_gold bound to gold_root
    store: MilvusIndexStore,
    embed_client: EmbedClient,                          # protocol — Fake in tests
    model_version: str,                                 # config models.embedder
    run_id: str = "",
) -> IndexMetrics:
```

Per `candidate_id`:

1. `gold = read_gold(cid)`; `None` → skip defensively.
2. `gold.is_tombstoned` → `store.delete([cid])`, `tombstoned_removed += 1`, continue (IDX-7).
3. `not is_indexable(gold)` → log `index.thin_skip` (PII-safe), `thin_skipped += 1`, continue (IDX-8).
4. **Gate (IDX-6):** if `store.fetch_versions([cid]).get(cid) == (gold.gold_hash, model_version)` →
   `skipped_unchanged += 1`, continue. *(May be batched once per run — see note.)*
5. `embed_text = build_embed_text(gold)` (PII-free by construction, AD-084 — no scan).
6. `vec = embed_client.embed([embed_text], mode="passage")[0]` (768-dim, normalized).
7. `record = build_record(gold, embed_text=embed_text, dense_vector=vec,
   skill_set=build_skill_set(gold), model_version=model_version)`; `store.upsert([record])`;
   `indexed += 1`.

Returns `IndexMetrics`. No leak/exit path — `dsm.pii` is not imported this slice (AD-084).

> **Note (perf, optional):** `fetch_versions` may be called once for the whole id set before the loop
> (single query) rather than per-candidate; the per-candidate form is shown for clarity. Embeds may be
> batched into one `embed([...], mode="passage")` call for the candidates that pass the gate (a-004
> recommends batching) — an implementation detail that does not change behavior or tests.

### `IndexMetrics` (`dsm/index/models.py`)

```python
class IndexMetrics(BaseModel):
    indexed: int = 0
    skipped_unchanged: int = 0
    tombstoned_removed: int = 0
    thin_skipped: int = 0
```

Index-local logging via a `structlog.get_logger("dsm.index")`; counts/`candidate_id` tokens only,
never name/email (mirrors `dsm.ingest.lineage`).

## CLI (`dsm/cli/commands.py` + `dsm/cli/main.py`)

New `index` command, registered as `app.command("index")(index)`:

```python
def index(gold_dir=_GOLD_DEFAULT, db_path=<config>, run_id="") -> None:
```

1. Load config; `idx_cfg = config["index"]["milvus"]` → `db_path`, `collection`, `dense_metric`;
   `model_version = config["models"]["embedder"]`.
2. `store = MilvusIndexStore(db_path, collection, dim=768, metric=dense_metric)` →
   `ensure_collection()`.
3. `embed_client = ModalEmbedClient()` (CLI tests monkeypatch this to a `FakeEmbedClient`, like the
   ingest predictors).
4. `metrics = index_gold(list_gold_ids(gold_dir), read_gold=partial(read_gold, gold_root=gold_dir),
   store=store, embed_client=embed_client, model_version=model_version, run_id=...)`.
5. Print the `── Index ──` summary; per-candidate lines = `candidate_id` token + structured fields.

No `DSM_CANDIDATE_ID_KEY` guard and no bronze read (AD-084: no per-candidate identity needed —
`embed_text` is PII-free by construction). `dsm match` and `dsm/index/stub.py` are **untouched**
(a-006 swaps the retriever).

## Config (`config/default.yaml`)

```yaml
index:
  milvus: { db_path: "data/index/milvus.db", collection: "candidates", dense_metric: "IP" }
```

`models.embedder` is already set (`BAAI/bge-base-en-v1.5`, AD-074) and is the `model_version`.

## Dependency + gitignore

- `pyproject.toml`: add `milvus-lite` to `dependencies`; `uv lock`. (AD-083.) Probe confirmed it
  installs + runs offline on this platform (macOS, py3.12).
- `.gitignore`: `data/index/*` + `!data/index/.gitkeep` (mirrors the bronze/silver/gold layers).

## Modules touched

| Module | New/Edit | Responsibility |
|---|---|---|
| `dsm/index/models.py` | **new** | `CandidateIndexRecord`, projection helpers, `IndexMetrics` |
| `dsm/index/text_builder.py` | **new** | pure `build_embed_text` / `build_skill_set` / `included_skills` |
| `dsm/index/milvus_store.py` | **new** | `MilvusIndexStore` (ensure/upsert/delete/fetch_versions) |
| `dsm/index/indexer.py` | **new** | `index_gold` orchestration + metrics |
| `dsm/cli/commands.py` | **edit** | `index` command |
| `dsm/cli/main.py` | **edit** | register `dsm index` |
| `config/default.yaml` | **edit** | `index` block |
| `pyproject.toml` / `uv.lock` | **edit** | add `milvus-lite` |
| `.gitignore` | **edit** | `data/index/` |
| `docs/decision.md` | **edit** | AD-081 / AD-082 / AD-083 / AD-084 |
| `dsm/index/stub.py` | **untouched** | a-006 replaces it |

## Testing strategy (no network — NF-1)

- **`FakeEmbedClient`** (tests/index, implements `EmbedClient`): deterministic 768-dim L2-normalized
  vector derived from the text (e.g. seeded from `hashlib.sha256(text)`), so dim + normalization are
  assertable and runs are reproducible. May record received `(texts, mode)` to assert `mode="passage"`.
- **text_builder** — `embed_text` is PII-free by construction (gold with vault refs set → neither ref
  appears); excludes `demonstrated is False`; identical for identical gold; order-insensitive (shuffled
  skill input → same output). `skill_set` excludes refuted skills.
- **models/projection** — filter-field mapping for each availability variant + Remote-India
  (`city=None`); `is_indexable` False on missing grade/location/availability (thin-skip).
- **milvus_store** (tmp `milvus.db` via `tmp_path`, in-process) — upsert then re-upsert ⇒ one entity,
  dense dim 768; `delete` removes a tombstoned id; `fetch_versions` returns stored `(gold_hash,
  model_version)`; BM25 sparse auto-generated (collection accepts insert without `sparse`).
- **indexer** (Fake client + tmp store) — first run indexes; second identical run ⇒ all
  `skipped_unchanged` (Fake records **no** new embed); bumping `model_version` ⇒ re-embedded;
  tombstoned gold ⇒ `delete` + `tombstoned_removed`; thin gold ⇒ `thin_skipped`.
- **CLI** — monkeypatch `ModalEmbedClient` → `FakeEmbedClient`, seed a tmp gold dir; assert the
  `── Index ──` summary text + exit code 0. (No bronze / `DSM_CANDIDATE_ID_KEY` needed.)
