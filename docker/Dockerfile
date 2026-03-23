FROM python:3.11-slim

WORKDIR /app

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency declaration and install deps only (cached layer)
COPY pyproject.toml .
RUN uv pip install --system --no-cache \
    "fastapi>=0.115.0" \
    "uvicorn[standard]>=0.30.0" \
    "httpx[socks]>=0.27.0" \
    "loguru>=0.7.0" \
    "pydantic>=2.0.0" \
    "pydantic-settings>=2.0.0" \
    "sqlalchemy>=2.0.0" \
    "python-dotenv>=1.0.0" \
    && mkdir -p data logs

# Copy source code (changes frequently, doesn't invalidate dependency layer)
COPY app/ app/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
