"""Ollama-based gabarito (answer key) parser.

Receives extracted PDF text (from Docling) and asks the local model to return
the caderno/answer structure as JSON.  Uses the same ``/api/chat`` endpoint
and ``format: json`` mode as the question enricher.

Never raises.  Returns an empty list on any error.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app.config import settings
from app.pipeline.parsers.gabarito import CadernoAnswers

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
Você é um especialista em gabaritos do ENARE (Exame Nacional de Residência em \
Enfermagem e demais áreas da saúde). Extraia todos os dados de gabarito do texto \
fornecido. Responda SEMPRE e APENAS com um objeto JSON válido, sem texto adicional.\
"""

_USER_TEMPLATE = """\
O texto abaixo foi extraído de um PDF de gabarito definitivo do ENARE.
Extraia TODOS os cadernos e seus gabaritos completos.

REGRAS:
- O nome do caderno aparece como cabeçalho, ex: "Residência Multi/Uniprofissional - Enfermagem - 3 - Turno Tarde"
- As questões 1-4 de cada caderno aparecem ANTES do cabeçalho no texto
- As questões 5-100 aparecem DEPOIS do cabeçalho
- Os números podem aparecer fora de ordem na grade — mapeie corretamente número → resposta
- Questões anuladas são marcadas com * — use null no JSON
- Inclua TODAS as 100 questões (1 a 100) de cada caderno

Retorne EXATAMENTE este JSON:
{{
  "cadernos": [
    {{
      "name": "nome exato do caderno",
      "answers": {{
        "1": "D",
        "2": "C",
        "46": null,
        "100": "E"
      }}
    }}
  ]
}}

TEXTO DO GABARITO:
{text}
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
        if not name or not isinstance(raw_answers, dict):
            continue

        answers: dict[int, str | None] = {}
        for key, val in raw_answers.items():
            try:
                num = int(key)
            except (TypeError, ValueError):
                continue
            if 1 <= num <= 100:
                answers[num] = _validate_answer(val)

        if answers:
            cadernos.append(CadernoAnswers(name=name, answers=answers))

    return cadernos


async def parse_gabarito_with_ollama(text: str) -> list[CadernoAnswers]:
    """Send extracted gabarito text to the local Ollama model and return answer maps.

    Returns an empty list on any error.
    """
    user_prompt = _USER_TEMPLATE.format(text=text)
    payload = {
        "model": settings.OLLAMA_MODEL,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }

    timeout = max(settings.OLLAMA_TIMEOUT_SECONDS * 2, 180)
    logger.info(
        "parse_gabarito_with_ollama: sending %d chars to Ollama (model=%s timeout=%ds)",
        len(text),
        settings.OLLAMA_MODEL,
        timeout,
    )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.OLLAMA_BASE_URL}/api/chat",
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            raw_content = data.get("message", {}).get("content", "")
    except httpx.TimeoutException:
        logger.warning("parse_gabarito_with_ollama: request timed out after %ds", timeout)
        return []
    except httpx.HTTPError as exc:
        logger.warning("parse_gabarito_with_ollama: HTTP error: %s", exc)
        return []
    except Exception as exc:  # noqa: BLE001
        logger.warning("parse_gabarito_with_ollama: unexpected error: %s", exc)
        return []

    parsed = _extract_json(raw_content)
    if parsed is None:
        logger.warning("parse_gabarito_with_ollama: failed to parse JSON from Ollama response")
        return []

    cadernos = _build_cadernos(parsed)
    logger.info(
        "parse_gabarito_with_ollama: extracted %d cadernos, answer counts=%s",
        len(cadernos),
        [len(c.answers) for c in cadernos],
    )
    return cadernos
