# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.11.28 AS uv

FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=uv /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE alembic.ini ./
COPY apps ./apps
COPY migrations ./migrations
COPY robolake ./robolake

RUN uv sync --frozen --no-dev --no-editable

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["/bin/sh", "-c", "alembic upgrade head && exec uvicorn apps.api.main:app --host 0.0.0.0 --port 8000"]
