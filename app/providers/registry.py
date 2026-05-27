"""Provider registry — maps provider names to their implementation classes."""

from __future__ import annotations

import logging
from typing import Any

from app.providers.anthropic import AnthropicProvider
from app.providers.base import AIProvider
from app.providers.deepseek import DeepSeekProvider
from app.providers.gemini import GeminiProvider
from app.providers.openai import OpenAIProvider

logger = logging.getLogger(__name__)

# Ordered list of all registered providers (order determines UI display order).
_REGISTERED: list[type[AIProvider]] = [
    GeminiProvider,
    OpenAIProvider,
    DeepSeekProvider,
    AnthropicProvider,
]

_REGISTRY: dict[str, type[AIProvider]] = {cls.name: cls for cls in _REGISTERED}


def get_provider_class(name: str) -> type[AIProvider]:
    """Return the provider class for *name*, raising ValueError if unknown."""
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"Unknown provider {name!r}. Available: {list(_REGISTRY)}"
        ) from None


def build_provider(
    name: str,
    api_key: str,
    *,
    model: str | None = None,
    timeout_seconds: int = 60,
    concurrency: int = 5,
) -> AIProvider:
    """Instantiate the named provider with the given credentials."""
    cls = get_provider_class(name)
    return cls(
        api_key=api_key,
        model=model,
        timeout_seconds=timeout_seconds,
        concurrency=concurrency,
    )


def list_providers() -> list[dict[str, Any]]:
    """Return metadata for every registered provider (for the settings UI)."""
    return [
        {
            "name": cls.name,
            "label": cls.label,
            "default_model": cls.default_model,
        }
        for cls in _REGISTERED
    ]
