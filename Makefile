.PHONY: check lint format typecheck test eval imports docker docker-dev

check: format lint typecheck test imports eval

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
	@echo "eval suite not yet configured"

docker:
	docker compose build app

docker-dev:
	docker compose build dev
