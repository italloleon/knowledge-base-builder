"""Gemini-based exam cover page extractor.

Reads the first page of a PDF (via its extracted markdown) and extracts
structured metadata: nome, periodo, tipo, cor, tipo_prova.

Never raises. Returns None on any error.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_SYSTEM_PROMPT = """\
Você é um assistente especializado em extrair metadados da capa de provas de residência médica \
e multiprofissional do Brasil (ENARE, FIOCRUZ, EINSTEIN, etc.).

Dado o texto extraído da capa do documento, identifique os campos solicitados.
Responda SOMENTE com um objeto JSON válido, sem texto adicional, sem markdown.\
"""

_USER_TEMPLATE = """\
Extraia os metadados da capa desta prova a partir do texto abaixo.

## TEXTO DA CAPA

{cover_text}

---

## TAREFA

Retorne EXATAMENTE este objeto JSON (use null para campos não encontrados):

{{
  "nome": "<nome ou título da prova, ex: 'ENARE 2024 Prova Objetiva'>",
  "periodo": "<'manha' ou 'tarde' — null se não informado>",
  "tipo": <número inteiro do tipo da prova (1, 2, 3 ou 4) — null se não informado>,
  "cor": "<cor da prova, ex: 'Azul', 'Amarelo', 'Verde', 'Rosa' — null se não informada>",
  "tipo_prova": "<tipo/modalidade da prova, ex: 'Multiprofissional Enfermagem', 'Uniprofissional' — null se não informado>"
}}

Regras:
- periodo deve ser exatamente 'manha' ou 'tarde' (minúsculas, sem acento), ou null.
- tipo deve ser um inteiro, não uma string.
- Se o texto mencionar 'Tarde' → periodo='tarde'; 'Manhã' → periodo='manha'.
- Para tipo_prova, combine modalidade e área se ambas estiverem presentes \
(ex: 'Multiprofissional' + 'Enfermagem' → 'Multiprofissional Enfermagem').
\
"""


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


async def extract_cover_metadata(markdown: str) -> dict[str, Any] | None:
    """Extract exam cover metadata from the beginning of the PDF markdown.

    Args:
        markdown: Full markdown extracted from the PDF. Only the first ~1000
                  characters are sent — they reliably contain the cover page.

    Returns:
        Dict with keys: nome, periodo, tipo, cor, tipo_prova (all nullable).
        Returns None on any error. Never raises.
    """
    if not settings.GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY not set — skipping cover metadata extraction")
        return None

    # Take up to 4000 chars but stop before the first question block.
    # T041/AOCP layouts output sidebar columns before the full-width header,
    # so the useful metadata may not be in the first 1200 chars.
    _QUESTION_START = re.search(
        r"\n\s*(?:Competências|Conhecimentos\s+Gerais|Conhecimentos\s+Específicos)",
        markdown[:6000],
        re.IGNORECASE,
    )
    cutoff = _QUESTION_START.start() if _QUESTION_START else 4000
    cover_text = markdown[:cutoff].strip()
    if not cover_text:
        return None

    url = _GEMINI_URL.format(model=settings.GEMINI_MODEL)
    payload = {
        "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
        "contents": [
            {
                "role": "user",
                "parts": [{"text": _USER_TEMPLATE.format(cover_text=cover_text)}],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.0,
        },
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json=payload,
                headers={"x-goog-api-key": settings.GEMINI_API_KEY},
                timeout=30.0,
            )
            if response.status_code == 429:
                logger.warning("cover extractor: Gemini rate limit (429)")
                return None
            response.raise_for_status()
            data = response.json()
            raw_content = data["candidates"][0]["content"]["parts"][0]["text"]
    except httpx.TimeoutException:
        logger.warning("cover extractor: Gemini request timed out")
        return None
    except httpx.HTTPStatusError as exc:
        logger.warning("cover extractor: Gemini HTTP error %d", exc.response.status_code)
        return None
    except (KeyError, IndexError):
        logger.warning("cover extractor: unexpected Gemini response shape")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("cover extractor: unexpected error: %s", exc)
        return None

    parsed = _extract_json(raw_content)
    if parsed is None:
        logger.warning("cover extractor: failed to parse JSON from Gemini response")
        return None

    result: dict[str, Any] = {
        "nome": parsed.get("nome") or None,
        "periodo": parsed.get("periodo") or None,
        "tipo": None,
        "cor": parsed.get("cor") or None,
        "tipo_prova": parsed.get("tipo_prova") or None,
    }

    raw_tipo = parsed.get("tipo")
    if raw_tipo is not None:
        try:
            result["tipo"] = int(raw_tipo)
        except (TypeError, ValueError):
            pass

    if result["periodo"] not in ("manha", "tarde"):
        result["periodo"] = None

    logger.info(
        "cover extractor: nome=%r periodo=%r tipo=%r cor=%r tipo_prova=%r",
        result["nome"],
        result["periodo"],
        result["tipo"],
        result["cor"],
        result["tipo_prova"],
    )
    return result
