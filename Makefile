.PHONY: check check-all lint format typecheck test eval imports smoke docker docker-dev

check: format lint typecheck test imports

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
	uv run pytest tests/ -v

imports:
	uv run lint-imports

eval:
	@echo "SKIP: eval suite not configured yet (Promptfoo + DeepEval — see docs/tech.md)"
	@exit 1

docker:
	docker compose build app

docker-dev:
	docker compose build dev
