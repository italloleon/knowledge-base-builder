FROM python:3.12-slim

# ---- System dependencies + non-root user (uid/gid 1000) ----
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 appgroup \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /bin/bash appuser

# ---- Install uv for fast dependency installation (pinned version) ----
COPY --from=ghcr.io/astral-sh/uv:0.7.3 /uv /usr/local/bin/uv

WORKDIR /app

# ---- Install Python dependencies ----
# Copy only pyproject.toml first so this layer is cached independently of source changes.
COPY pyproject.toml ./

RUN uv pip install --system --no-cache \
    "fastapi>=0.115" \
    "uvicorn[standard]>=0.30" \
    "sqlalchemy[asyncio]>=2.0" \
    "alembic>=1.13" \
    "asyncpg>=0.29" \
    "arq>=0.26" \
    "pydantic-settings>=2.0" \
    "httpx>=0.27" \
    "pymupdf>=1.24" \
    "pdfminer.six>=20221105" \
    "python-multipart>=0.0.9" \
    "aiofiles>=23.0" \
    "redis>=5.0" \
    "python-jose[cryptography]>=3.3" \
    "bcrypt>=4.0" \
    "email-validator>=2.0"

# ---- Copy application source ----
COPY app/ ./app/
COPY worker/ ./worker/
COPY migrations/ ./migrations/
COPY alembic.ini ./
COPY scripts/ ./scripts/

# ---- Permissions ----
RUN chmod +x /app/scripts/migrate.sh \
    && mkdir -p /app/uploads \
    && chown -R appuser:appgroup /app

# ---- Switch to non-root user ----
USER appuser

# ---- Default command (overridden per service in docker-compose) ----
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
