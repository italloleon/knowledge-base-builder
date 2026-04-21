FROM python:3.12-slim

# ---- System dependencies + non-root user (uid/gid 1000) ----
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 appgroup \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /bin/bash appuser

# ---- Install uv for fast dependency installation ----
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

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
    "docling>=2.0" \
    "python-multipart>=0.0.9" \
    "aiofiles>=23.0" \
    "redis>=5.0"

# ---- Pre-download Docling models so the container runs fully offline ----
ENV DOCLING_ARTIFACTS_PATH=/opt/docling_models
RUN mkdir -p /opt/docling_models \
    && docling-tools models download --output-dir /opt/docling_models

# ---- Copy application source ----
COPY app/ ./app/
COPY worker/ ./worker/
COPY migrations/ ./migrations/
COPY alembic.ini ./
COPY scripts/ ./scripts/

# ---- Ensure scripts are executable, uploads dir exists, and model cache is writable ----
# rapidocr downloads .pth/.onnx models lazily into its own package directory on
# first use; chown lets appuser write there without needing root at runtime.
RUN chmod +x /app/scripts/migrate.sh \
    && mkdir -p /app/uploads \
    && mkdir -p /usr/local/lib/python3.12/site-packages/rapidocr/models \
    && chown -R appuser:appgroup /app \
    && chown -R appuser:appgroup /opt/docling_models \
    && chown -R appuser:appgroup /usr/local/lib/python3.12/site-packages/rapidocr \
    && chmod -R a+rX /usr/local/lib/python3.12/site-packages/

# ---- Switch to non-root user ----
USER appuser

# ---- Default command (overridden per service in docker-compose) ----
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
