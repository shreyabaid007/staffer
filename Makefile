.PHONY: check lint format typecheck test eval docker docker-dev

check: format lint typecheck test eval

format:
	uv run ruff format

lint:
	uv run ruff check --fix

typecheck:
	uv run pyright

test:
	uv run pytest tests/ -v

eval:
	@echo "eval suite not yet configured"

docker:
	docker compose build app

docker-dev:
	docker compose build dev
