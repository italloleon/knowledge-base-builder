"""FastAPI application factory."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.domains.flashcards.router import router as flashcards_router
from app.domains.forum.router import router as forum_router
from app.routers import auth, editais, exams, gabarito, health, imports, ingestion, jobs, opinions, study, users
from app.routers import settings as settings_router

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
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
    )

    # Routers
    application.include_router(health.router, prefix="/api")
    application.include_router(auth.router, prefix="/api")
    application.include_router(users.router, prefix="/api")
    application.include_router(ingestion.router, prefix="/api")
    application.include_router(imports.router, prefix="/api")
    application.include_router(jobs.router, prefix="/api")
    application.include_router(exams.router, prefix="/api")
    application.include_router(editais.router, prefix="/api")
    application.include_router(gabarito.router, prefix="/api")
    application.include_router(opinions.router, prefix="/api")
    application.include_router(study.router, prefix="/api")
    application.include_router(settings_router.router, prefix="/api")
    application.include_router(flashcards_router, prefix="/api")
    application.include_router(forum_router, prefix="/api")

    return application


app = create_app()
