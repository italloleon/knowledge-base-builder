"""Gemini-based enrichment for Edital (exam notice) documents.

Sends the full extracted markdown to Gemini in a single call and returns a
populated EditalData dataclass.  Never raises — returns partial (possibly
empty) data on any error.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import replace
from typing import Any

import httpx

from app.config import settings
from app.pipeline.base import EditalData

logger = logging.getLogger(__name__)

_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_SYSTEM_PROMPT = """\
Você é um especialista em editais de programas de residência em saúde no Brasil,
com amplo conhecimento sobre o ENARE (Exame Nacional de Residência em Enfermagem
e demais áreas da saúde) e sobre as normas da SGTES/Ministério da Saúde.
Extraia com precisão todos os dados estruturados solicitados do edital fornecido.
Responda SEMPRE e APENAS com um objeto JSON válido, sem texto adicional.\
"""

_USER_TEMPLATE = """\
Analise o edital de residência em saúde abaixo e extraia TODOS os campos listados.

EDITAL (em markdown):
{markdown}

Retorne um objeto JSON com exatamente esta estrutura (use null para campos não encontrados):

{{
  "numero_edital": "número/código do edital, ex: '001/2024'",
  "ano": 2024,
  "edition_name": "nome da edição do processo seletivo, ex: 'ENARE 2024'",
  "organizadora": "nome da entidade que organiza o processo seletivo",
  "instituicao_gestora": "nome da instituição gestora principal",
  "modalidade": "modalidade do programa, ex: 'Residência Multiprofissional em Saúde'",
  "total_questoes_gerais": 20,
  "total_questoes_especificas": 30,
  "percentual_minimo_aprovacao": 60.0,
  "bolsa_mensal": 3330.43,
  "data_inicio_programas": "data de início prevista para os programas, ex: '2024-03-01'",
  "contato_email": "e-mail de contato oficial",
  "contato_telefone": "telefone de contato oficial",
  "url_enare": "URL oficial do site do ENARE ou processo seletivo",
  "cronograma": [
    {{
      "evento": "nome do evento/etapa",
      "data_inicio": "YYYY-MM-DD",
      "data_fim": "YYYY-MM-DD ou null se for data única"
    }}
  ],
  "vagas": [
    {{
      "profissao": "nome da profissão",
      "instituicao": "nome da instituição ofertante",
      "cidade": "cidade",
      "estado": "UF de dois caracteres",
      "programa": "nome completo do programa",
      "vagas_ampla": 2,
      "vagas_reservadas": {{}}
    }}
  ],
  "instituicoes": [
    {{
      "nome": "nome completo da instituição",
      "sigla": "sigla",
      "cidade": "cidade",
      "estado": "UF",
      "programas": ["lista de nomes de programas oferecidos"]
    }}
  ],
  "knowledge_areas": [
    {{
      "profissao": "ENFERMAGEM",
      "gerais": [
        {{
          "area": "SAÚDE COLETIVA",
          "topicos": [
            "Políticas de saúde no Brasil: SUS e seus princípios",
            "Epidemiologia geral"
          ]
        }}
      ],
      "especificos": [
        {{
          "area": "FUNDAMENTOS DE ENFERMAGEM",
          "topicos": [
            "Sistematização da Assistência de Enfermagem (SAE)",
            "Processo de Enfermagem"
          ]
        }}
      ]
    }}
  ]
}}

INSTRUÇÕES IMPORTANTES:
- Para knowledge_areas: extraia TODAS as profissões listadas no edital (normalmente no Anexo III
  ou seção de conteúdo programático). Para cada profissão, liste TODAS as áreas e TODOS os
  tópicos/subtópicos exatamente como aparecem no documento. Não resuma nem omita tópicos.
  Se não houver conteúdo programático no documento, retorne uma lista vazia [].
- Para cronograma: converta todas as datas para o formato YYYY-MM-DD.
- Para bolsa_mensal e percentual_minimo_aprovacao: retorne apenas o número, sem símbolos.
- Para vagas_reservadas: use um objeto com chaves descritivas, ex: {{"cotas_raciais": 1}}.
- Todos os campos de texto devem ser strings. Números devem ser int ou float conforme o tipo.
- Se um campo não for encontrado no documento, use null (não use string vazia "").\
"""

_SYSTEM_ANEXO_III = """\
Você extrai o conteúdo programático (Anexo III, matriz de conteúdo ou equivalente) \
de editais ENARE / residência em saúde. Responda SOMENTE com JSON válido, sem prefixos.\
"""

_USER_ANEXO_III = """\
O texto abaixo é o trecho do edital com conteúdo programático por profissão (geralmente Anexo III).

Extraia a lista "knowledge_areas" com TODAS as profissões, TODAS as áreas e TODOS os tópicos, \
exatamente como no documento (não resuma, não agregue tópicos). Se o trecho \
não tiver tabelas de conteúdo programático, retorne {{"knowledge_areas": []}}.

TRECHO:
{chunk}

Estrutura exigida (JSON):
{{
  "knowledge_areas": [
    {{
      "profissao": "NOME EM MAIÚSCULAS ex: ENFERMAGEM",
      "gerais": [{{"area": "NOME", "topicos": ["tópico1", "tópico2"]}}],
      "especificos": [{{"area": "NOME", "topicos": ["tópico1"]}}]
    }}
  ]
}}
"""


def _slice_for_knowledge_content(markdown: str) -> str:
    """Prefer a window around Anexo III / conteúdo programático; else the tail of the doc."""
    cap = 200_000
    for pattern in (
        r"(?i)(anexo\s*iii|anexo\s*3[.\s:\-]|\bIII\s*[-–]?\s*conte[úu]do)",
        r"(?i)conte[úu]do\s+program[áa]tico",
    ):
        m = re.search(pattern, markdown)
        if m is not None:
            start = m.start()
            rest = markdown[start:]
            end_m = re.search(
                r"(?i)\n\s*(anexo\s*(iv|v|4|5)\b|sum[áa]rio|sumario|refer[êe]ncias)",
                rest[500:],
            )
            if end_m:
                rest = rest[: 500 + end_m.start()]
            return rest[:cap]
    n = len(markdown)
    if n > 12_000:
        return markdown[int(n * 0.50) :][:cap]
    return markdown


def _extract_json(text: str) -> dict[str, Any] | None:
    """Try to parse JSON from a raw text response, with fallback regex extraction."""
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


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _safe_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    return []


def _build_edital_data(data: dict[str, Any]) -> EditalData:
    """Map a raw Gemini JSON response dict onto an EditalData dataclass."""
    return EditalData(
        numero_edital=_safe_str(data.get("numero_edital")),
        ano=_safe_int(data.get("ano")),
        edition_name=_safe_str(data.get("edition_name")),
        organizadora=_safe_str(data.get("organizadora")),
        instituicao_gestora=_safe_str(data.get("instituicao_gestora")),
        modalidade=_safe_str(data.get("modalidade")),
        total_questoes_gerais=_safe_int(data.get("total_questoes_gerais")),
        total_questoes_especificas=_safe_int(data.get("total_questoes_especificas")),
        percentual_minimo_aprovacao=_safe_float(data.get("percentual_minimo_aprovacao")),
        bolsa_mensal=_safe_float(data.get("bolsa_mensal")),
        data_inicio_programas=_safe_str(data.get("data_inicio_programas")),
        contato_email=_safe_str(data.get("contato_email")),
        contato_telefone=_safe_str(data.get("contato_telefone")),
        url_enare=_safe_str(data.get("url_enare")),
        cronograma=_safe_list(data.get("cronograma")),
        vagas=_safe_list(data.get("vagas")),
        instituicoes=_safe_list(data.get("instituicoes")),
        knowledge_areas=_safe_list(data.get("knowledge_areas")),
    )


async def _call_gemini_json(
    system_prompt: str, user_text: str, timeout_seconds: int
) -> dict[str, Any] | None:
    """One Gemini generateContent call; returns parsed top-level JSON or None."""
    if not settings.GEMINI_API_KEY:
        return None
    url = _GEMINI_URL.format(model=settings.GEMINI_MODEL)
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [
            {
                "role": "user",
                "parts": [{"text": user_text}],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1,
        },
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json=payload,
                headers={"x-goog-api-key": settings.GEMINI_API_KEY},
                timeout=timeout_seconds,
            )
            if response.status_code == 429:
                logger.warning("Gemini rate limit (429) in _call_gemini_json")
                return None
            response.raise_for_status()
            resp_data = response.json()
            raw_content = resp_data["candidates"][0]["content"]["parts"][0]["text"]
    except httpx.TimeoutException:
        logger.warning("_call_gemini_json: request timed out after %ds", timeout_seconds)
        return None
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "_call_gemini_json: HTTP %d %s",
            exc.response.status_code,
            (exc.response.text or "")[:300],
        )
        return None
    except (KeyError, IndexError) as exc:
        logger.warning("_call_gemini_json: bad response shape: %s", exc)
        return None
    return _extract_json(raw_content)


async def enrich_edital(markdown: str) -> EditalData:
    """Send the full edital markdown to Gemini and return a populated EditalData.

    Uses a single API call with temperature=0.1 to extract all scalar metadata
    fields and the complete knowledge_areas structure in one pass.

    Never raises.  On any error, returns a partial (possibly empty) EditalData.
    """
    if not settings.GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY is not set — skipping edital enrichment")
        return EditalData()

    # Use a longer timeout: edital documents can be 100-150k chars
    timeout_seconds = max(settings.GEMINI_TIMEOUT_SECONDS * 4, 120)

    logger.info(
        "enrich_edital: sending %d chars to Gemini (model=%s timeout=%ds)",
        len(markdown),
        settings.GEMINI_MODEL,
        timeout_seconds,
    )
    parsed = await _call_gemini_json(
        _SYSTEM_PROMPT, _USER_TEMPLATE.format(markdown=markdown), timeout_seconds
    )
    if parsed is None:
        logger.warning("enrich_edital: failed to parse JSON from first Gemini response")
        return EditalData()

    result = _build_edital_data(parsed)

    # One-shot often omits the huge Anexo III — second pass on a focused slice
    if not result.knowledge_areas:
        chunk = _slice_for_knowledge_content(markdown)
        if len(chunk) >= 300:
            logger.info(
                "enrich_edital: anexo III follow-up, chunk length=%d",
                len(chunk),
            )
            anexo_parsed = await _call_gemini_json(
                _SYSTEM_ANEXO_III,
                _USER_ANEXO_III.format(chunk=chunk),
                timeout_seconds,
            )
            if anexo_parsed is not None:
                ka = _safe_list(anexo_parsed.get("knowledge_areas"))
                if ka:
                    result = replace(result, knowledge_areas=ka)

    logger.info(
        "enrich_edital: extracted knowledge_areas=%d cronograma=%d vagas=%d",
        len(result.knowledge_areas),
        len(result.cronograma),
        len(result.vagas),
    )
    return result
