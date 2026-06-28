.PHONY: check check-all lint format typecheck test eval eval-tier1 eval-record imports docs-check decisions-status contract-snapshot smoke docker docker-dev

check: format lint typecheck test eval-tier1 imports

# Cross-document invariants (decision-log integrity, no dangling ADR refs, steering docs vs
# config, frozen-contract snapshot). Runs inside `make check` automatically via the `test`
# target; this is the explicit entrypoint for running just the doc checks.
docs-check:
	uv run pytest tests/docs -v

# Print the decisions currently IN FORCE (derived from docs/decision.md — a view, never stored).
decisions-status:
	uv run python scripts/decisions_status.py

# Regenerate the frozen-contract JSON-schema baseline after an intended, ADR-backed model change.
contract-snapshot:
	UPDATE_CONTRACT_SNAPSHOT=1 uv run pytest tests/docs/test_frozen_contract.py -q

check-all: check eval

# Opt-in real-data smoke test over data/raw/ (PII-dense, gitignored; runs real Docling).
# Skips automatically if no files are present. Not part of `make check`.
smoke:
	DSM_REAL_SMOKE=1 uv run pytest tests/ingest/test_real_data_smoke.py -v

format:
	uv run ruff format

lint:
	uv run ruff check --fix

typecheck:
	uv run pyright

test:
	uv run pytest tests/ --ignore=tests/eval -v

imports:
	uv run lint-imports

eval:
	uv run pytest tests/eval -m "eval_offline or eval_live" -v

eval-tier1:
	uv run pytest tests/eval/test_invariants.py -v

eval-record:
	uv run python -m dsm.eval.record

docker:
	docker compose build app

docker-dev:
	docker compose build dev
