"""Gemini-based explanation enricher — generates gabarito comentado for nursing exam questions.

Produces a structured explanation (gabarito comentado) for each question, including:
- justification for the correct alternative
- per-distractor explanations
- central concept label
- confidence score

Never raises. Returns None on any error.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx

from app.config import settings
from app.pipeline.base import ParsedQuestion

logger = logging.getLogger(__name__)

_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_SYSTEM_PROMPT = """\
Você é um professor especialista em enfermagem com experiência em bancas de residências de \
enfermagem no Brasil (ENARE, FIOCRUZ, EINSTEIN, HU-UFSC). Sua função é redigir gabaritos \
comentados de alta qualidade para uso em plataformas de estudo.

Regras absolutas:
1. Você SEMPRE recebe o gabarito correto como input. Nunca questione ou ignore o gabarito.
2. Sua tarefa é EXPLICAR por que a alternativa correta está certa e por que cada distrator \
está errado — não é redescobrir a resposta.
3. Raciocine a partir da evidência clínica e da legislação vigente brasileira \
(COFEN, ANVISA, MS, PNSP).
4. Use linguagem técnica precisa, adequada ao nível de um candidato à residência de enfermagem.
5. Responda SOMENTE com um objeto JSON válido, sem texto adicional, sem markdown, sem blocos \
de código.\
"""

_USER_TEMPLATE = """\
## QUESTÃO

**Área:** {area}
**Tópico:** {topic}
**Competência geral:** {competencia_geral_area} — {competencia_geral_topico}
**Competência específica:** {competencia_especifica_area} — {competencia_especifica_topico}
**Nível Bloom:** {bloom_level}
**Dificuldade:** {difficulty}
**Termos-chave:** {keywords_csv}

**Enunciado:**
{enunciado}

**Alternativas:**
(A) {alt_a}
(B) {alt_b}
(C) {alt_c}
(D) {alt_d}
(E) {alt_e}

**GABARITO OFICIAL: {gabarito}**

---

## TAREFA

Produza o gabarito comentado desta questão. Retorne EXATAMENTE este objeto JSON:

{{
  "correta": "<letra do gabarito — DEVE ser {gabarito}>",
  "justificativa_correta": "<explicação em 3-5 frases...>",
  "justificativas_erradas": {{
    "<letra diferente de {gabarito}>": "<por que este distrator está errado...>",
    ...
  }},
  "conceito_central": "<substantivo ou sintagma nominal de 3-6 palavras>",
  "confidence": <0.0 a 1.0>
}}

Verificação obrigatória antes de responder: confirme que o campo "correta" contém exatamente \
a letra "{gabarito}". Se não contiver, corrija antes de retornar.\
"""

_UNSPECIFIED = "Não especificado"


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


def _build_user_prompt(question: ParsedQuestion, gabarito: str, enrichment: dict[str, Any] | None) -> str:
    """Render the user prompt template, falling back gracefully for missing enrichment fields."""
    meta = enrichment or {}

    area = meta.get("area") or _UNSPECIFIED
    topic = meta.get("topic") or _UNSPECIFIED
    competencia_geral_area = meta.get("competencia_geral_area") or _UNSPECIFIED
    competencia_geral_topico = meta.get("competencia_geral_topico") or _UNSPECIFIED
    competencia_especifica_area = meta.get("competencia_especifica_area") or _UNSPECIFIED
    competencia_especifica_topico = meta.get("competencia_especifica_topico") or _UNSPECIFIED
    bloom_level = meta.get("bloom_level") or _UNSPECIFIED
    difficulty = meta.get("difficulty") or _UNSPECIFIED

    raw_keywords = meta.get("keywords")
    if isinstance(raw_keywords, list):
        keywords_csv = ", ".join(str(k) for k in raw_keywords)
    else:
        keywords_csv = ""

    alts = question.alternatives
    return _USER_TEMPLATE.format(
        area=area,
        topic=topic,
        competencia_geral_area=competencia_geral_area,
        competencia_geral_topico=competencia_geral_topico,
        competencia_especifica_area=competencia_especifica_area,
        competencia_especifica_topico=competencia_especifica_topico,
        bloom_level=bloom_level,
        difficulty=difficulty,
        keywords_csv=keywords_csv,
        enunciado=question.enunciado[:2000],
        alt_a=alts.get("A", ""),
        alt_b=alts.get("B", ""),
        alt_c=alts.get("C", ""),
        alt_d=alts.get("D", ""),
        alt_e=alts.get("E", ""),
        gabarito=gabarito.upper(),
    )


def _build_result(raw: dict[str, Any], gabarito: str) -> dict[str, Any]:
    """Validate and normalise the LLM output into the final result dict."""
    correta = str(raw.get("correta", "")).strip().upper()
    justificativa_correta = str(raw.get("justificativa_correta", "")).strip()
    conceito_central = str(raw.get("conceito_central", "")).strip()

    try:
        confidence = float(raw.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.0

    # justificativas_erradas: expect a dict; tolerate an array of {letter, explanation}
    raw_erradas = raw.get("justificativas_erradas", {})
    if isinstance(raw_erradas, list):
        justificativas_erradas: dict[str, str] = {}
        for entry in raw_erradas:
            if isinstance(entry, dict):
                letter = str(entry.get("letter", entry.get("letra", ""))).strip().upper()
                explanation = str(entry.get("explanation", entry.get("explicacao", ""))).strip()
                if letter:
                    justificativas_erradas[letter] = explanation
    elif isinstance(raw_erradas, dict):
        justificativas_erradas = {
            str(k).strip().upper(): str(v).strip() for k, v in raw_erradas.items()
        }
    else:
        justificativas_erradas = {}

    # Self-consistency check
    expected = gabarito.upper()
    flagged = False
    if correta != expected:
        logger.warning(
            "Q explanation: self-consistency check failed — expected correta=%r, got %r. "
            "Flagging for review.",
            expected,
            correta,
        )
        flagged = True

    # Confidence gate
    if confidence < 0.7:
        flagged = True

    return {
        "correta": correta,
        "justificativa_correta": justificativa_correta,
        "justificativas_erradas": justificativas_erradas,
        "conceito_central": conceito_central,
        "confidence": confidence,
        "flagged": flagged,
    }


async def generate_explanation(
    question: ParsedQuestion,
    gabarito: str,
    enrichment: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Generate a gabarito comentado for a single question using Gemini.

    Args:
        question:   The parsed question (enunciado + alternatives).
        gabarito:   The official correct answer letter (e.g. "C").
        enrichment: Optional classification enrichment dict produced by the
                    classification enricher (area, topic, bloom_level, etc.).
                    Falls back to "Não especificado" for any missing field.

    Returns:
        A plain dict matching the QuestionExplanation schema, or None on error.
        Never raises.
    """
    if not settings.GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY is not set — skipping explanation generation")
        return None

    if not question.alternatives:
        logger.warning("Q%d: no alternatives — skipping explanation", question.number)
        return None

    user_prompt = _build_user_prompt(question, gabarito, enrichment)

    url = _GEMINI_URL.format(model=settings.GEMINI_MODEL)
    payload = {
        "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
        "contents": [
            {
                "role": "user",
                "parts": [{"text": user_prompt}],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.2,
        },
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json=payload,
                headers={"x-goog-api-key": settings.GEMINI_API_KEY},
                timeout=settings.GEMINI_TIMEOUT_SECONDS,
            )
            if response.status_code == 429:
                logger.warning(
                    "Q%d: Gemini rate limit (429) during explanation generation",
                    question.number,
                )
                return None
            response.raise_for_status()
            data = response.json()
            raw_content = data["candidates"][0]["content"]["parts"][0]["text"]
    except httpx.TimeoutException:
        logger.warning("Q%d: Gemini explanation request timed out", question.number)
        return None
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Q%d: Gemini HTTP error %d during explanation — %s",
            question.number,
            exc.response.status_code,
            (exc.response.text or "")[:300],
        )
        return None
    except (KeyError, IndexError):
        logger.warning("Q%d: unexpected Gemini response shape for explanation", question.number)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Q%d: unexpected Gemini explanation error: %s", question.number, exc)
        return None

    parsed = _extract_json(raw_content)
    if parsed is None:
        logger.warning(
            "Q%d: failed to parse JSON from Gemini explanation response", question.number
        )
        return None

    result = _build_result(parsed, gabarito)
    logger.info(
        "Q%d: explanation generated — correta=%s confidence=%.2f flagged=%s",
        question.number,
        result["correta"],
        result["confidence"],
        result["flagged"],
    )
    return result


async def generate_explanations(
    questions: list[ParsedQuestion],
    gabaritos: dict[int, str],
    enrichments: dict[int, dict[str, Any] | None] | None = None,
):
    """Async generator — yields (question_number, explanation_dict | None).

    Args:
        questions:   List of parsed questions to explain.
        gabaritos:   Mapping of question number → correct answer letter.
        enrichments: Optional mapping of question number → enrichment dict.

    Skips questions with no gabarito entry or no alternatives. Never raises.
    Uses Gemini concurrency from settings.
    """
    if not questions:
        return

    if not settings.GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY is not set — skipping explanation enrichment")
        for q in questions:
            yield q.number, None
        return

    enrichable = [q for q in questions if q.alternatives and q.number in gabaritos]
    total = len(enrichable)
    semaphore = asyncio.Semaphore(settings.GEMINI_ENRICHMENT_CONCURRENCY)

    async def _explain_one(q: ParsedQuestion) -> dict[str, Any] | None:
        async with semaphore:
            gabarito = gabaritos[q.number]
            enrichment = (enrichments or {}).get(q.number)
            try:
                return await generate_explanation(q, gabarito, enrichment)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Q%d: explanation raised: %s", q.number, exc)
                return None

    async with httpx.AsyncClient():
        for idx, q in enumerate(enrichable, start=1):
            logger.info(
                "Stage 5 (Gemini): generating explanation Q%d (%d/%d)",
                q.number,
                idx,
                total,
            )
            result = await _explain_one(q)
            yield q.number, result
