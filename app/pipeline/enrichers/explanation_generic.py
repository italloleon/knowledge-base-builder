"""Provider-agnostic explanation enricher — reuses all prompt/validation logic
from explanation_gemini but routes the HTTP call through any AIProvider.

For questions that have associated images (diagrams, ECG strips, etc.) the
images are loaded from disk and sent to the model via generate_json_with_images
so the model can actually see the visual context it needs to explain the question.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from app.config import settings as env_settings
from app.pipeline.base import ParsedQuestion
from app.pipeline.enrichers.explanation_gemini import (
    _SYSTEM_PROMPT,
    _build_result,
    _build_user_prompt,
)
from app.providers.base import AIProvider

logger = logging.getLogger(__name__)


def _load_question_images(image_refs: list[str]) -> list[bytes]:
    """Read image files from the uploads directory. Silently skips missing files."""
    result: list[bytes] = []
    upload_dir = Path(env_settings.UPLOAD_DIR)
    for ref in image_refs:
        path = upload_dir / ref
        if path.exists() and path.is_file():
            try:
                result.append(path.read_bytes())
            except OSError as exc:
                logger.warning("Could not read image %s: %s", ref, exc)
        else:
            logger.debug("Image not found on disk: %s", path)
    return result


async def generate_explanations(
    questions: list[ParsedQuestion],
    *,
    gabaritos: dict[int, str],
    enrichments: dict[int, dict | None],
    provider: AIProvider,
    concurrency: int = 3,
):
    """Async generator — yields (question_number, explanation_dict | None).

    Works with any AIProvider.  Never raises.
    """
    semaphore = asyncio.Semaphore(concurrency)
    total = len(questions)

    async def _one(q: ParsedQuestion) -> tuple[int, dict | None]:
        gabarito = gabaritos.get(q.number)
        if not gabarito:
            return q.number, None
        async with semaphore:
            prompt = _build_user_prompt(
                q, gabarito, enrichments.get(q.number), insight=None
            )
            image_bytes = _load_question_images(q.images or [])
            if image_bytes:
                logger.info(
                    "Q%d: sending %d image(s) to %s for explanation",
                    q.number, len(image_bytes), provider.name,
                )
                raw = await provider.generate_json_with_images(_SYSTEM_PROMPT, prompt, image_bytes)
            else:
                raw = await provider.generate_json(_SYSTEM_PROMPT, prompt)
            if raw is None:
                return q.number, None
            return q.number, _build_result(raw, gabarito)

    for idx, q in enumerate(questions, start=1):
        logger.info(
            "Explanation (%s): Q%d (%d/%d)", provider.name, q.number, idx, total
        )
        try:
            q_number, result = await _one(q)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Q%d: explanation raised: %s", q.number, exc)
            q_number, result = q.number, None
        yield q_number, result


async def generate_explanation(
    question: ParsedQuestion,
    gabarito: str,
    *,
    enrichment: dict[str, Any] | None = None,
    insight: str | None = None,
    provider: AIProvider,
) -> dict[str, Any] | None:
    """Single-question variant used by the refine endpoint."""
    prompt = _build_user_prompt(question, gabarito, enrichment, insight=insight)
    image_bytes = _load_question_images(question.images or [])
    if image_bytes:
        logger.info(
            "Q%d: sending %d image(s) to %s for explanation (refine)",
            question.number, len(image_bytes), provider.name,
        )
        raw = await provider.generate_json_with_images(_SYSTEM_PROMPT, prompt, image_bytes)
    else:
        raw = await provider.generate_json(_SYSTEM_PROMPT, prompt)
    if raw is None:
        return None
    return _build_result(raw, gabarito)
