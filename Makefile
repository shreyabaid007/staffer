.PHONY: check check-all lint format typecheck test eval imports docker docker-dev

check: format lint typecheck test imports

check-all: check eval

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
