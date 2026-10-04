FROM ghcr.io/astral-sh/uv:0.6.2 AS uv
FROM python:3.13-slim-bookworm

COPY --from=uv /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    LLM_WIKI_DIR=/app/wiki \
    LLM_WIKI_SOURCES=/app/sources \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

COPY scripts ./scripts
COPY sources ./sources
COPY wiki ./wiki

CMD ["python", "scripts/serve_api.py"]
