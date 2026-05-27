"""Provider-agnostic edital enricher.

Reuses all prompts, JSON schema and parsing helpers from edital_gemini but
routes API calls through any AIProvider.  Never raises — returns partial
EditalData on any error.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from app.pipeline.base import EditalData
from app.pipeline.enrichers.edital_gemini import (
    _SYSTEM_ANEXO_III,
    _SYSTEM_PROMPT,
    _USER_ANEXO_III,
    _USER_TEMPLATE,
    _build_edital_data,
    _slice_for_knowledge_content,
)
from app.providers.base import AIProvider

logger = logging.getLogger(__name__)


async def enrich_edital(markdown: str, *, provider: AIProvider) -> EditalData:
    """Send edital markdown to *provider* and return a populated EditalData.

    Uses the same two-pass strategy as the Gemini-specific implementation:
    1. Full document → all scalar fields + knowledge_areas in one call.
    2. If knowledge_areas is empty, a focused second call on the Anexo III slice.

    Never raises.  Returns partial (possibly empty) EditalData on any error.
    """
    timeout = max(provider.timeout_seconds, 120)
    logger.info(
        "enrich_edital [%s]: sending %d chars (timeout=%ds)",
        provider.name,
        len(markdown),
        timeout,
    )

    # ── Pass 1: full document ──────────────────────────────────────────────
    parsed = await provider.generate_json(
        _SYSTEM_PROMPT,
        _USER_TEMPLATE.format(markdown=markdown),
    )
    if parsed is None:
        logger.warning("enrich_edital [%s]: pass-1 returned nothing", provider.name)
        return EditalData()

    result = _build_edital_data(parsed)

    # ── Pass 2: focused Anexo III slice (only when knowledge_areas missing) ──
    if not result.knowledge_areas:
        chunk = _slice_for_knowledge_content(markdown)
        if len(chunk) >= 300:
            logger.info(
                "enrich_edital [%s]: pass-2 (Anexo III slice, %d chars)",
                provider.name,
                len(chunk),
            )
            anexo_parsed = await provider.generate_json(
                _SYSTEM_ANEXO_III,
                _USER_ANEXO_III.format(chunk=chunk),
            )
            if anexo_parsed is not None:
                from app.pipeline.enrichers.edital_gemini import _safe_list  # noqa: PLC0415
                ka = _safe_list(anexo_parsed.get("knowledge_areas"))
                if ka:
                    result = replace(result, knowledge_areas=ka)

    logger.info(
        "enrich_edital [%s]: knowledge_areas=%d cronograma=%d vagas=%d",
        provider.name,
        len(result.knowledge_areas),
        len(result.cronograma),
        len(result.vagas),
    )
    return result
