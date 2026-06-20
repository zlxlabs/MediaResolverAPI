FROM python:3.11-slim

WORKDIR /app

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install dependencies from pyproject.toml (single source of truth — no duplicated
# dependency list). hatchling needs the package source present to build, so copy
# metadata + source together, then install.
COPY pyproject.toml README.md ./
COPY app/ app/
RUN uv pip install --system --no-cache . \
    && mkdir -p data logs

# Run as a non-root user.
# NOTE: data/ and logs/ are bind-mounted from the host in docker-compose. The host
# directories must be writable by this uid (10001), e.g. `chown -R 10001:10001 data logs`,
# otherwise SQLite/log writes will fail.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
