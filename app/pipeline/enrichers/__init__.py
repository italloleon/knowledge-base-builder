from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from app.pipeline.base import ParsedQuestion


def get_enricher(provider: str | None = None):
    """Return the enrich_questions async generator function for the given provider.

    provider: "ollama" | "gemini" | None (uses ENRICHMENT_PROVIDER from config)
    """
    from app.config import settings  # noqa: PLC0415

    p = (provider or settings.ENRICHMENT_PROVIDER).strip().lower()
    if p == "gemini":
        from app.pipeline.enrichers.gemini import enrich_questions  # noqa: PLC0415
    else:
        from app.pipeline.enrichers.ollama import enrich_questions  # noqa: PLC0415
    return enrich_questions
