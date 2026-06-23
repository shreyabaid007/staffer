.PHONY: check check-all lint format typecheck test eval eval-tier1 eval-record imports smoke docker docker-dev

check: format lint typecheck test eval-tier1 imports

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
