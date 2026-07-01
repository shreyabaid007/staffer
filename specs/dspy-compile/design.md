# dspy-compile — Design

## Overview
Offline MIPROv2 compilation of the query-time DSPy signatures (`CandidateScoring`,
`RoleClarification`, `RoleIntakeSignature`, `NearMissRationale`) against the golden set,
with versioned artefact storage and A/B runtime loading.

## Architecture

### New module: `dsm/compile.py`
The compilation module. Imports `dspy`, `dsm.eval.golden_set`, `dsm.eval.faithfulness`,
`dsm.eval.retrieval_quality`. Does NOT import `dsm.pii`, `dsm.ingest`, `dsm.index`, or `modal`.

```
dsm/compile.py
  golden_to_examples(cases) -> list[dspy.Example]
  build_metric(judge, role_fixtures) -> Callable
  compile_signature(module, trainset, metric, teacher_lm, auto) -> compiled_module
  next_version(artifacts_dir, name) -> int
  save_compiled(module, artifacts_dir, name, version) -> Path
  load_compiled(path) -> dspy.Module
```

### Golden set → trainset adapter
Each `GoldenSetCase` maps to a `dspy.Example`:
- `input_fields`: the role fixture's structured fields (title, skills, location, etc.)
- `expected_output`: the `expected_shortlist` + `faithfulness_labels`

The adapter reads the golden set JSON, filters to `review_status == "signed_off"`,
and builds the trainset.

### Metric adapter
Wraps the existing eval tools:
1. **Faithfulness**: `judge_narrative()` from `dsm/eval/faithfulness.py` — scores narrative
   grounding. Returns the G-Eval score (0-10 mapped to 0-1).
2. **Recall@K**: from `dsm/eval/retrieval_quality.py` — deterministic set overlap.
3. **Combined**: weighted average (`0.6 * faithfulness + 0.4 * recall`) — this is a tunable
   MIPROv2 metric, not a production scoring weight.

### MIPROv2 configuration
```python
optimizer = MIPROv2(
    metric=combined_metric,
    auto="light",        # conservative: 21 cases < 50 typical minimum
    num_threads=4,
    teacher_settings=dict(lm=teacher_lm),
)
compiled = optimizer.compile(module, trainset=trainset)
```

The teacher LM is a stronger model (e.g. `openrouter/anthropic/claude-opus-4-6`) configured
via `config/default.yaml::compile.teacher_model`. The student (deployed) model is the current
`models.reasoning_llm`.

### Artefact storage
```
artifacts/
  compiled_CandidateScoring_v1.json
  compiled_CandidateScoring_v2.json
  compiled_RoleClarification_v1.json
  ...
```

- Auto-incrementing version per signature name.
- `.gitignore`d (artefacts contain optimised prompt text, not code).
- CI can promote known-good versions by committing them explicitly.
- `Module.save()` / `dspy.load()` for round-trip stability.

### Runtime loading
`dsm match --compiled artifacts/compiled_CandidateScoring_v1.json`:
- The CLI loads the compiled module and injects it as the predictor (replacing the default
  `make_score_predictor`).
- The `config_snapshot` on the result gains a `compiled_version` field (the artefact filename).
- Without `--compiled`, the hand-written predictor is used as today.

### Derivation-version scheme (AD-066 extension)
The full cache/determinism key becomes:
`(prompt_version, compiled_version, model_version)`
where `compiled_version` is `None` when using hand-written prompts.

## Rejected alternatives
- **Auto-deploy compiled artefacts**: breaks the determinism invariant (AD-066) — a human must
  explicitly promote.
- **Compile ingest signatures**: ingest is write-time; compilation optimises query-time latency
  and quality. Ingest signatures are deferred.
- **Large trainset augmentation**: 21 cases is small for MIPROv2; `auto="light"` acknowledges
  this. Expanding the golden set is a separate effort.
- **Custom metrics**: the existing faithfulness + Recall@K are sufficient for v1.

## Impact on existing code
- `dsm/cli/commands.py`: new `compile` command + `--compiled` option on `match`/`explain`.
- `dsm/cli/main.py`: register `compile` command.
- `config/default.yaml`: add `compile.teacher_model` key.
- `pyproject.toml`: new import contract for `dsm.compile`.
- No changes to `dsm/models.py`, `dsm/match/`, `dsm/pii/`, or `dsm/index/`.
