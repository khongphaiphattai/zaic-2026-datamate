# syntax=docker/dockerfile:1
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_CACHE_DIR=/tmp/uv-cache

# 1. Install locked OSI runtime dependencies first for layer caching
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# 2. Copy package source and container entrypoint, then install non-editable
COPY src/ ./src/
COPY scripts/ ./scripts/
RUN uv sync --frozen --no-dev --no-editable \
    && /app/.venv/bin/python -m compileall -q /app/src /app/scripts \
    && rm -rf /tmp/uv-cache

# 3. Runtime environment for offline (--network none), read-only /data, /tmp scratch, /result output
RUN mkdir -p /data /result /tmp && chmod 1777 /tmp

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=42 \
    TMPDIR=/tmp \
    TEMP=/tmp \
    TMP=/tmp \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp \
    DUCKDB_TEMP_DIRECTORY=/tmp \
    UV_OFFLINE=1 \
    ZAIC_DATA_DIR=/data \
    ZAIC_RESULT_DIR=/result \
    ZAIC_TEMP_DIR=/tmp

ENTRYPOINT ["/app/.venv/bin/python", "/app/scripts/run_container.py"]
