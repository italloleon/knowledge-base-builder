"""Ollama-based question enrichment — Stage 4 of the pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx

from app.config import settings
from app.pipeline.base import ParsedQuestion
from app.pipeline.enrichers.taxonomy import enforce_taxonomy, taxonomy_for_prompt

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
Você é um especialista em enfermagem e em concursos de residência médica no Brasil.
Analise questões de provas de residência de enfermagem e classifique-as de forma estruturada.
Responda SEMPRE e APENAS com um objeto JSON válido, sem texto adicional.\
"""

_USER_TEMPLATE = """\
Analise a seguinte questão de enfermagem:

ENUNCIADO:
{enunciado}

ALTERNATIVAS:
{alternatives}

Retorne um objeto JSON com exatamente estes campos:
{{
  "area": "grande área de enfermagem",
  "topic": "tópico específico dentro da área",
  "competencia_geral_area": "área da competência geral do edital",
  "competencia_geral_topico": "item/tópico de Competências Gerais do edital",
  "competencia_especifica_area": "área da competência específica do edital",
  "competencia_especifica_topico": "item/tópico de Competências Específicas do edital",
  "keywords": ["lista", "de", "termos-chave", "relevantes", "da", "questão"],
  "difficulty": "facil, medio ou dificil",
  "bloom_level": "nível taxonômico de Bloom: conhecimento, compreensão, aplicação, análise, síntese ou avaliação"
}}\
"""

_TAXONOMY_TEMPLATE = """\

TAXONOMIA OFICIAL DO EDITAL (use somente estes valores para classificar):
{taxonomy_json}

REGRAS OBRIGATÓRIAS DE CLASSIFICAÇÃO:
- Preencha os campos "competencia_geral_area" e "competencia_geral_topico" com valores EXATOS de competencias_gerais.
- Preencha os campos "competencia_especifica_area" e "competencia_especifica_topico" com valores EXATOS de competencias_especificas.
- O campo "area/topic" deve refletir a melhor área/tópico principal da questão na taxonomia oficial.
- Nunca invente nomes fora da taxonomia.
"""


def _format_alternatives(alternatives: dict[str, str]) -> str:
    return "\n".join(f"({k}) {v}" for k, v in sorted(alternatives.items()))


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Extract first JSON object from the response
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


async def _enrich_one(
    client: httpx.AsyncClient,
    question: ParsedQuestion,
    semaphore: asyncio.Semaphore,
    taxonomy_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    async with semaphore:
        enunciado = question.enunciado[:2000]  # cap to avoid huge prompts
        alternatives_text = _format_alternatives(question.alternatives)
        user_prompt = _USER_TEMPLATE.format(
            enunciado=enunciado,
            alternatives=alternatives_text,
        )
        if taxonomy_context:
            user_prompt += _TAXONOMY_TEMPLATE.format(
                taxonomy_json=taxonomy_for_prompt(taxonomy_context)
            )

        payload = {
            "model": settings.OLLAMA_MODEL,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
        }

        try:
            response = await client.post(
                f"{settings.OLLAMA_BASE_URL}/api/chat",
                json=payload,
                timeout=settings.OLLAMA_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()
            raw_content = data.get("message", {}).get("content", "")
            enrichment = _extract_json(raw_content)
            enrichment = enforce_taxonomy(enrichment, taxonomy_context)
            if enrichment is None:
                logger.warning("Q%d: failed to parse JSON from Ollama response", question.number)
            return enrichment
        except httpx.TimeoutException:
            logger.warning("Q%d: Ollama request timed out", question.number)
        except httpx.HTTPError as exc:
            logger.warning("Q%d: Ollama HTTP error: %s", question.number, exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Q%d: unexpected enrichment error: %s", question.number, exc)
        return None


async def enrich_questions(
    questions: list[ParsedQuestion],
    taxonomy_context: dict[str, Any] | None = None,
):
    """Async generator — yields (question_number, enrichment_dict | None) one at a time.

    Processes questions sequentially (Ollama is single-threaded).
    Skips questions with no alternatives. Never raises.
    """
    if not questions:
        return

    enrichable = [q for q in questions if q.alternatives]
    total = len(enrichable)
    semaphore = asyncio.Semaphore(settings.OLLAMA_ENRICHMENT_CONCURRENCY)

    async with httpx.AsyncClient() as client:
        for idx, q in enumerate(enrichable, start=1):
            logger.info("Stage 4: enriching Q%d (%d/%d)", q.number, idx, total)
            try:
                result = await _enrich_one(client, q, semaphore, taxonomy_context)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Q%d: enrichment raised: %s", q.number, exc)
                result = None
            yield q.number, result
