"""Provider-agnostic question enricher using the AIProvider abstraction."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.pipeline.base import ParsedQuestion
from app.pipeline.enrichers.taxonomy import enforce_taxonomy, taxonomy_for_prompt
from app.providers.base import AIProvider

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
  "keywords": ["lista", "de", "termos-chave"],
  "difficulty": "facil, medio ou dificil",
  "bloom_level": "conhecimento, compreensão, aplicação, análise, síntese ou avaliação"
}}\
"""

_TAXONOMY_SUFFIX = """\

TAXONOMIA OFICIAL DO EDITAL (use somente estes valores):
{taxonomy_json}

REGRAS: preencha competencia_* com valores EXATOS da taxonomia. Nunca invente nomes.\
"""


def _format_alternatives(alternatives: dict[str, str]) -> str:
    return "\n".join(f"({k}) {v}" for k, v in sorted(alternatives.items()))


async def enrich_questions(
    questions: list[ParsedQuestion],
    taxonomy_context: dict[str, Any] | None = None,
    *,
    provider: AIProvider,
    concurrency: int = 5,
):
    """Async generator — yields (question_number, enrichment_dict | None).

    Works with any AIProvider implementation. Skips questions with no alternatives.
    Never raises.
    """
    if not questions:
        return

    enrichable = [q for q in questions if q.alternatives]
    total = len(enrichable)
    semaphore = asyncio.Semaphore(concurrency)

    async def _enrich_one(q: ParsedQuestion) -> tuple[int, dict | None]:
        async with semaphore:
            user_prompt = _USER_TEMPLATE.format(
                enunciado=q.enunciado[:2000],
                alternatives=_format_alternatives(q.alternatives),
            )
            if taxonomy_context:
                user_prompt += _TAXONOMY_SUFFIX.format(
                    taxonomy_json=taxonomy_for_prompt(taxonomy_context)
                )

            result = await provider.generate_json(_SYSTEM_PROMPT, user_prompt)
            if result is not None:
                result = enforce_taxonomy(result, taxonomy_context)
            if result is None:
                logger.warning("Q%d: [%s] enrichment returned nothing", q.number, provider.name)
            return q.number, result

    for idx, q in enumerate(enrichable, start=1):
        logger.info(
            "Stage 4 (%s): enriching Q%d (%d/%d)", provider.name, q.number, idx, total
        )
        try:
            q_number, enrichment = await _enrich_one(q)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Q%d: enrichment raised: %s", q.number, exc)
            q_number, enrichment = q.number, None
        yield q_number, enrichment
