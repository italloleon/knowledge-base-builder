"""FastAPI application factory."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.middleware.auth import ApiKeyMiddleware
from app.routers import exams, health, imports, ingestion, jobs

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def create_app() -> FastAPI:
    application = FastAPI(
        title="Knowledge Base Builder",
        description="ENARE exam PDF ingestion and deterministic parsing service",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    allowed_origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
    application.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "X-API-Key"],
    )

    # API key auth (passthrough when AUTH_ENABLED=false)
    application.add_middleware(ApiKeyMiddleware)

    # Routers
    application.include_router(health.router)
    application.include_router(ingestion.router)
    application.include_router(imports.router)
    application.include_router(jobs.router)
    application.include_router(exams.router)

    return application


app = create_app()
