"""Anthropic Claude provider."""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

import httpx

from app.providers.base import AIProvider

logger = logging.getLogger(__name__)


class AnthropicProvider(AIProvider):
    name = "anthropic"
    label = "Anthropic Claude"
    default_model = "claude-haiku-4-5-20251001"

    _BASE_URL = "https://api.anthropic.com/v1/messages"
    _API_VERSION = "2023-06-01"

    async def generate_json(
        self,
        system_prompt: str,
        user_text: str,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 8192,
            "temperature": 0.1,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_text}],
        }
        return await self._post(payload)

    async def generate_json_with_images(
        self,
        system_prompt: str,
        user_text: str,
        images: list[bytes],
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        image_content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.standard_b64encode(img).decode(),
                },
            }
            for img in images
        ]
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 8192,
            "temperature": 0.1,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": [*image_content, {"type": "text", "text": user_text}]},
            ],
        }
        return await self._post(payload)

    async def generate_json_with_pdf(
        self,
        system_prompt: str,
        user_text: str,
        pdf_bytes: bytes,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        logger.warning("[%s] PDF-based generation is not supported — falling back to text", self.name)
        return None

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self._BASE_URL,
                    json=payload,
                    headers={
                        "x-api-key": self.api_key,
                        "anthropic-version": self._API_VERSION,
                        "content-type": "application/json",
                    },
                    timeout=self.timeout_seconds,
                )
                if response.status_code == 429:
                    self._log_rate_limit()
                    return None
                response.raise_for_status()
                data = response.json()
                raw = data["content"][0]["text"]
        except httpx.TimeoutException:
            self._log_timeout()
            return None
        except httpx.HTTPStatusError as exc:
            self._log_http_error(exc.response.status_code, exc.response.text or "")
            return None
        except (KeyError, IndexError) as exc:
            logger.warning("[%s] unexpected response shape: %s", self.name, exc)
            return None

        return self._extract_json(raw)

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        return None
