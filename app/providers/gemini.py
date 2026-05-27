"""Google Gemini provider."""

from __future__ import annotations

import base64
import json
import re
from typing import Any

import httpx

from app.providers.base import AIProvider


class GeminiProvider(AIProvider):
    name = "gemini"
    label = "Google Gemini"
    default_model = "gemini-2.5-flash"

    _BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    # ------------------------------------------------------------------

    async def generate_json(
        self,
        system_prompt: str,
        user_text: str,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        payload = self._build_payload(
            system_prompt=system_prompt,
            parts=[{"text": user_text}],
            response_schema=response_schema,
        )
        return await self._post(payload)

    async def generate_json_with_images(
        self,
        system_prompt: str,
        user_text: str,
        images: list[bytes],
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        image_parts = [
            {"inlineData": {"mimeType": "image/png", "data": base64.standard_b64encode(img).decode()}}
            for img in images
        ]
        payload = self._build_payload(
            system_prompt=system_prompt,
            parts=[*image_parts, {"text": user_text}],
            response_schema=response_schema,
        )
        return await self._post(payload)

    async def generate_json_with_pdf(
        self,
        system_prompt: str,
        user_text: str,
        pdf_bytes: bytes,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        pdf_b64 = base64.standard_b64encode(pdf_bytes).decode()
        payload = self._build_payload(
            system_prompt=system_prompt,
            parts=[
                {"inlineData": {"mimeType": "application/pdf", "data": pdf_b64}},
                {"text": user_text},
            ],
            response_schema=response_schema,
        )
        return await self._post(payload)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_payload(
        self,
        system_prompt: str,
        parts: list[dict],
        response_schema: dict[str, Any] | None,
    ) -> dict[str, Any]:
        gen_cfg: dict[str, Any] = {
            "responseMimeType": "application/json",
            "temperature": 0.1,
        }
        if response_schema:
            gen_cfg["responseSchema"] = response_schema

        return {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": gen_cfg,
        }

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        url = self._BASE_URL.format(model=self.model)
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers={"x-goog-api-key": self.api_key},
                    timeout=self.timeout_seconds,
                )
                if response.status_code == 429:
                    self._log_rate_limit()
                    return None
                response.raise_for_status()
                resp_data = response.json()
                candidate = resp_data["candidates"][0]
                finish_reason = candidate.get("finishReason", "")
                if finish_reason not in ("STOP", ""):
                    self._log_http_error(0, f"finishReason={finish_reason}")
                raw = candidate["content"]["parts"][0]["text"]
        except httpx.TimeoutException:
            self._log_timeout()
            return None
        except httpx.HTTPStatusError as exc:
            self._log_http_error(exc.response.status_code, exc.response.text or "")
            return None
        except (KeyError, IndexError) as exc:
            self._log_parse_error(str(exc))
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
