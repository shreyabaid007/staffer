# dspy-compile — Tasks

## T-000 — ADR gate
Write ADR entries in `docs/decision.md` for the compilation decision. **Stop for review.**

## T-001 — Golden set adapter
File: `dsm/compile.py`
- Implement `golden_to_examples()`: load the golden set, filter to signed-off cases, map each
  `GoldenSetCase` to a `dspy.Example` with the role fixture fields as inputs and expected
  shortlist/labels as the expected output.
- Test: unit test with a synthetic 3-case golden set.

## T-002 — Metric adapter
File: `dsm/compile.py`
- Implement `build_metric()`: wrap the faithfulness judge and Recall@K into a single MIPROv2
  metric callable `(example, prediction, trace=None) -> float`.
- Faithfulness: call `judge_narrative()`, scale G-Eval 1-10 to 0-1.
- Recall@K: call the deterministic `recall_at_k()`.
- Combined: `0.6 * faithfulness + 0.4 * recall`.
- Test: unit test with mock judge + known recall values.

## T-003 — Compile function
File: `dsm/compile.py`
- Implement `compile_signature()`: instantiate MIPROv2 with `auto="light"`, `num_threads=4`,
  teacher settings from config. Run `optimizer.compile()` and return the compiled module.
- Implement `next_version()` and `save_compiled()` for artefact versioning.
- Implement `load_compiled()` wrapping `dspy.load()`.
- Test: unit test with a fake module + fake optimizer (no live LLM).

## T-004 — CLI `dsm compile` command
File: `dsm/cli/commands.py`, `dsm/cli/main.py`
- Add `compile` command: `dsm compile --signature <name>` runs the compilation pipeline.
- Signature names: `score`, `clarify`, `intake`, `near-miss-rationale`.
- Build the teacher LM from `config.compile.teacher_model`.
- Print the artefact path on success.
- Register in `main.py`.

## T-005 — Runtime `--compiled` flag
File: `dsm/cli/commands.py`
- Add `--compiled` option to `match` and `explain` commands.
- When set, load the compiled module via `load_compiled()` and use it as the predictor instead
  of the hand-written one.
- Add `compiled_version` to `_config_snapshot()`.

## T-006 — Config + import contract
Files: `config/default.yaml`, `pyproject.toml`
- Add `compile.teacher_model` to config (default: `openrouter/anthropic/claude-opus-4-6`).
- Add `.gitignore` entry for `artifacts/`.
- Add import-linter contract: `dsm.compile` must not import `dsm.pii`, `dsm.ingest`, `dsm.index`,
  or `modal`.

## T-007 — Integration test
File: `tests/eval/test_compile.py`
- Key-gated (`eval_live`) integration test: compile `CandidateScoring` on 3 golden cases,
  save/load round-trip, verify the loaded module produces valid output.
- Offline unit test: verify `golden_to_examples` + `build_metric` + `next_version` with mocks.
