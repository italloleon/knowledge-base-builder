from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.pipeline.base import ParsedQuestion


def get_enricher(provider: str | None = None, api_key: str | None = None, model: str | None = None):
    """Return an ``enrich_questions`` async-generator for the given provider.

    For Ollama (local) no API key is needed.
    For all cloud providers the caller must supply ``api_key`` (read from DB
    settings or env var by the task before calling this helper).
    """
    from app.config import settings as env_settings  # noqa: PLC0415

    p = (provider or env_settings.ENRICHMENT_PROVIDER).strip().lower()

    if p == "ollama":
        from app.pipeline.enrichers.ollama import enrich_questions  # noqa: PLC0415
        return enrich_questions

    if p == "gemini" and api_key is None:
        # Legacy path: gemini enricher reads the key from env itself
        from app.pipeline.enrichers.gemini import enrich_questions  # noqa: PLC0415
        return enrich_questions

    # Generic path for all cloud providers (gemini with explicit key, openai, deepseek, anthropic…)
    from app.pipeline.enrichers.generic import enrich_questions as _generic  # noqa: PLC0415
    from app.providers.registry import build_provider  # noqa: PLC0415

    key = api_key or getattr(env_settings, f"{p.upper()}_API_KEY", "") or ""
    if not key:
        raise ValueError(
            f"No API key configured for provider {p!r}. "
            "Set it in Settings or via the corresponding environment variable."
        )

    provider_instance = build_provider(p, api_key=key, model=model)

    # Bind the provider instance so the caller can use the same signature:
    # async for q_number, data in enrich_questions(questions, taxonomy_context=...):
    async def _bound(
        questions: list[Any],
        taxonomy_context: dict | None = None,
    ):
        async for item in _generic(
            questions,
            taxonomy_context=taxonomy_context,
            provider=provider_instance,
        ):
            yield item

    return _bound
