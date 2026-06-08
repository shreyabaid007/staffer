FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir uv

WORKDIR /app

# Install dependencies first (cached layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy source
COPY . .
RUN uv sync --frozen --no-dev

# Download spaCy model required by Presidio
RUN uv run python -m spacy download en_core_web_lg

# ---------- dev target (includes test/lint tooling) ----------
FROM base AS dev
RUN uv sync --frozen
ENTRYPOINT ["uv", "run"]
CMD ["python", "-m", "pytest", "tests/", "-v"]

# ---------- production target ----------
FROM base AS prod
ENTRYPOINT ["uv", "run"]
CMD ["python", "-m", "dsm.cli"]
