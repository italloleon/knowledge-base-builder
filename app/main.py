"""FastAPI application factory."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.middleware.auth import ApiKeyMiddleware
from app.routers import exams, health, ingestion, jobs

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def create_app() -> FastAPI:
    application = FastAPI(
        title="Knowledge Base Builder",
        description="ENARE exam PDF ingestion and deterministic parsing service",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS — permissive for development; tighten in production
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API key auth (passthrough when AUTH_ENABLED=false)
    application.add_middleware(ApiKeyMiddleware)

    # Routers
    application.include_router(health.router)
    application.include_router(ingestion.router)
    application.include_router(jobs.router)
    application.include_router(exams.router)

    return application


app = create_app()
