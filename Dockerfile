FROM python:3.13-slim AS app-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:/usr/local/bin:${PATH}"

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
COPY scripts/ ./scripts/

RUN uv sync --no-dev --locked


FROM app-base AS contracts

RUN python scripts/export_openapi.py


FROM node:22-bookworm-slim AS frontend-build

WORKDIR /app/frontend

RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./
COPY --from=contracts /app/openapi.json /app/openapi.json
RUN pnpm run generate:types && pnpm run build


FROM app-base AS app

COPY --from=frontend-build /app/frontend/dist ./frontend/dist

RUN mkdir -p data_input data_output logs .cache/legaldoc_extraction

EXPOSE 8000

CMD ["uvicorn", "legaldoc_extraction.api:app", "--host", "0.0.0.0", "--port", "8000"]
