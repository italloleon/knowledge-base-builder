"""DeepSeek provider — OpenAI-compatible API."""

from __future__ import annotations

from app.providers.openai import OpenAIProvider


class DeepSeekProvider(OpenAIProvider):
    """DeepSeek uses an OpenAI-compatible chat completions endpoint."""

    name = "deepseek"
    label = "DeepSeek"
    default_model = "deepseek-chat"

    _BASE_URL = "https://api.deepseek.com/v1/chat/completions"
