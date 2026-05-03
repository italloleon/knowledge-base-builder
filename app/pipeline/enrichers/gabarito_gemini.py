"""Gemini-based gabarito (answer key) parser.

Sends the raw PDF bytes directly to Gemini as inline data so it can visually
parse the grid layout — no text extraction step required.  This is more
reliable than regex because grid-format PDFs produce messy text when extracted
by Docling or pdfminer.

The answer list uses an array of {number, answer} objects rather than a
dynamic-key object because the Gemini responseSchema does not support
additionalProperties for map types.

Never raises.  Returns an empty list on any error.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

import httpx

from app.config import settings
from app.pipeline.parsers.gabarito import CadernoAnswers

logger = logging.getLogger(__name__)

_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_SYSTEM_PROMPT = """\
Você é um especialista em gabaritos do ENARE (Exame Nacional de Residência em \
Enfermagem e demais áreas da saúde). Extraia todos os dados de gabarito do documento \
PDF fornecido. Responda SEMPRE e APENAS com um objeto JSON válido, sem texto adicional.\
"""

_USER_PROMPT = """\
O PDF anexado é o gabarito definitivo do ENARE. Extraia TODOS os cadernos presentes.

Para cada caderno:
- O nome do caderno aparece como um cabeçalho, ex: \
"Residência Multi/Uniprofissional - Enfermagem - 3 - Turno Tarde"
- As questões 1 a 4 normalmente aparecem ANTES do cabeçalho do caderno (na mesma página)
- As questões 5 a 100 aparecem DEPOIS do cabeçalho em formato de grade

Regras:
- Questões anuladas são marcadas com * — use null no campo "answer"
- Inclua TODAS as 100 questões de cada caderno (1 a 100)
- Os números podem aparecer fora de ordem na grade — mapeie corretamente número → resposta

Retorne um JSON com a seguinte estrutura:
{
  "cadernos": [
    {
      "name": "nome exato do caderno",
      "answers": [
        {"number": 1, "answer": "D"},
        {"number": 46, "answer": null},
        {"number": 100, "answer": "E"}
      ]
    }
  ]
}
"""

# Gemini responseSchema — uses ARRAY of {number, answer} objects because
# the API does not support additionalProperties for dynamic-key maps.
_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "required": ["cadernos"],
    "properties": {
        "cadernos": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "required": ["name", "answers"],
                "properties": {
                    "name": {"type": "STRING"},
                    "answers": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "required": ["number", "answer"],
                            "properties": {
                                "number": {"type": "INTEGER"},
                                "answer": {"type": "STRING", "nullable": True},
                            },
                        },
                    },
                },
            },
        }
    },
}


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


def _validate_answer(val: Any) -> str | None:
    if val is None:
        return None
    s = str(val).strip().upper()
    return s if s in ("A", "B", "C", "D", "E") else None


def _build_cadernos(data: dict[str, Any]) -> list[CadernoAnswers]:
    cadernos: list[CadernoAnswers] = []
    raw_list = data.get("cadernos")
    if not isinstance(raw_list, list):
        return cadernos

    # SECURITY: Bound the number of cadernos accepted from the LLM response to
    # prevent a malicious or runaway model from producing thousands of entries
    # that exhaust memory during downstream processing.
    for item in raw_list[:50]:
        if not isinstance(item, dict):
            continue
        # SECURITY: Truncate the caderno name sourced from the LLM so it cannot
        # inflate warning strings or response fields beyond a reasonable length.
        name = str(item.get("name", "")).strip()[:200]
        raw_answers = item.get("answers")
        if not name or not isinstance(raw_answers, list):
            continue

        answers: dict[int, str | None] = {}
        for entry in raw_answers:
            if not isinstance(entry, dict):
                continue
            try:
                num = int(entry["number"])
            except (KeyError, TypeError, ValueError):
                continue
            if 1 <= num <= 100:
                answers[num] = _validate_answer(entry.get("answer"))

        if answers:
            cadernos.append(CadernoAnswers(name=name, answers=answers))

    return cadernos


async def parse_gabarito_with_gemini(pdf_bytes: bytes) -> list[CadernoAnswers]:
    """Send raw PDF bytes to Gemini and return per-caderno answer maps.

    Returns an empty list when GEMINI_API_KEY is unset or on any API error.
    """
    if not settings.GEMINI_API_KEY:
        logger.warning("parse_gabarito_with_gemini: GEMINI_API_KEY not set — skipping")
        return []

    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode()
    url = _GEMINI_URL.format(model=settings.GEMINI_MODEL)
    payload = {
        "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"inlineData": {"mimeType": "application/pdf", "data": pdf_b64}},
                    {"text": _USER_PROMPT},
                ],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _RESPONSE_SCHEMA,
            "temperature": 0.0,
        },
    }

    timeout = max(settings.GEMINI_TIMEOUT_SECONDS * 3, 90)
    logger.info(
        "parse_gabarito_with_gemini: sending %d KB PDF to Gemini (model=%s timeout=%ds)",
        len(pdf_bytes) // 1024,
        settings.GEMINI_MODEL,
        timeout,
    )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json=payload,
                headers={"x-goog-api-key": settings.GEMINI_API_KEY},
                timeout=timeout,
            )
            if response.status_code == 429:
                logger.warning("parse_gabarito_with_gemini: Gemini rate limit (429)")
                return []
            response.raise_for_status()
            resp_data = response.json()
            raw_content = resp_data["candidates"][0]["content"]["parts"][0]["text"]
    except httpx.TimeoutException:
        logger.warning("parse_gabarito_with_gemini: request timed out after %ds", timeout)
        return []
    except httpx.HTTPStatusError as exc:
        # SECURITY: Log only the first 300 chars of the Gemini error body.
        # The full body could be large, and in pathological cases may echo back
        # request data.  The API key is sent as a header and never appears in
        # the response body, but we keep the truncation as a general safeguard.
        logger.warning(
            "parse_gabarito_with_gemini: HTTP %d — %s",
            exc.response.status_code,
            (exc.response.text or "")[:300],
        )
        return []
    except (KeyError, IndexError) as exc:
        logger.warning("parse_gabarito_with_gemini: unexpected response shape: %s", exc)
        return []
    except Exception as exc:  # noqa: BLE001
        logger.warning("parse_gabarito_with_gemini: unexpected error: %s", exc)
        return []

    parsed = _extract_json(raw_content)
    if parsed is None:
        logger.warning("parse_gabarito_with_gemini: failed to parse JSON from Gemini response")
        return []

    cadernos = _build_cadernos(parsed)
    logger.info(
        "parse_gabarito_with_gemini: extracted %d cadernos, answer counts=%s",
        len(cadernos),
        [len(c.answers) for c in cadernos],
    )
    return cadernos
