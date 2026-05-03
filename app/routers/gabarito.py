"""Gabarito (answer key) upload, parsing, and application endpoints."""

import asyncio
import logging
import uuid
from functools import partial

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.config import settings
from app.database import get_session
from app.models import Exam, Question
from app.pipeline.enrichers.gabarito_gemini import parse_gabarito_with_gemini
from app.pipeline.enrichers.gabarito_ollama import parse_gabarito_with_ollama
from app.pipeline.parsers.gabarito import parse_gabarito
from app.pipeline.parsers.gabarito_validator import cross_validate
from app.schemas import (
    ApplyGabaritoRequest,
    ApplyGabaritoResponse,
    GabaritoCaderno,
    GabaritoParseResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["gabarito"])

# Gabarito PDFs are typically < 500 KB. Cap at the global limit but also enforce
# a hard ceiling of 20 MB so a mis-configured MAX_UPLOAD_SIZE_MB cannot allow
# unbounded allocations from this endpoint.
_MAX_BYTES = min(settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024, 20 * 1024 * 1024)

# Maximum number of characters that a single warning string may contain.
# Prevents a malicious/corrupted LLM response from inflating the response body.
_MAX_WARNING_LEN = 200

# Maximum number of warnings surfaced per caderno to prevent response bloat.
_MAX_WARNINGS_PER_CADERNO = 50


def _extract_text_sync(pdf_bytes: bytes) -> str | None:
    """Synchronous pdfminer extraction — must only be called via run_in_executor.

    pdfminer preserves the raw column-separated layout that the gabarito
    regex parser expects.  Docling's ML-based layout analysis re-flows tables
    in a way that can drop caderno sections, so it is not used here.
    """
    try:
        from io import BytesIO

        from pdfminer.high_level import extract_text as pdfminer_extract

        text = pdfminer_extract(BytesIO(pdf_bytes))
        if text and len(text.strip()) >= 100:
            return text
        logger.warning("_extract_text: pdfminer returned suspiciously short output")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("_extract_text: pdfminer extraction failed: %s", exc)
        return None


async def _extract_text(pdf_bytes: bytes) -> str | None:
    """Run pdfminer in a thread pool so it cannot block the event loop.

    pdfminer is CPU/IO bound and not async-aware.  Running it directly in an
    async handler stalls the entire uvicorn worker for the duration of parsing,
    which can be several seconds on large or malformed PDFs.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(_extract_text_sync, pdf_bytes))


@router.post("/gabarito/parse", response_model=GabaritoParseResponse)
async def parse_gabarito_pdf(file: UploadFile = File(...)):
    """Upload a gabarito PDF and extract the answer map for every caderno.

    Provider selection follows ENRICHMENT_PROVIDER:
    - ``gemini``: PDF sent directly to Gemini for visual parsing (no text extraction needed).
    - ``ollama``: text extracted via pdfminer then sent to the local Ollama model.
    Falls back to pdfminer + regex parsing if the chosen LLM returns nothing.

    Returns a preview of all cadernos found. No data is written to the
    database. Use POST /exams/{exam_id}/gabarito to apply one caderno's
    answers to a parsed exam.
    """
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Only PDF files are accepted")

    # SECURITY: Read up to _MAX_BYTES + 1 so we can detect oversized uploads
    # without buffering the entire file into memory first.  file.read() with no
    # argument would buffer an arbitrarily large upload before any size check.
    content = await file.read(_MAX_BYTES + 1)
    if not content:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")
    if len(content) > _MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum allowed size of {_MAX_BYTES // (1024 * 1024)} MB",
        )
    if not content.startswith(b"%PDF"):
        raise HTTPException(status_code=422, detail="File does not appear to be a valid PDF")

    cadernos = []
    markdown: str | None = None
    provider = settings.ENRICHMENT_PROVIDER
    llm_used = False

    if provider == "gemini" and settings.GEMINI_API_KEY:
        cadernos = await parse_gabarito_with_gemini(content)
        if not cadernos:
            logger.warning("Gemini gabarito parse returned nothing — falling back to regex")
        else:
            llm_used = True

    elif provider == "ollama":
        markdown = await _extract_text(content)
        if markdown:
            cadernos = await parse_gabarito_with_ollama(markdown)
        if not cadernos:
            logger.warning("Ollama gabarito parse returned nothing — falling back to regex")
        else:
            llm_used = True

    # Fallback: pdfminer extraction + regex parser (reuse extracted text if available)
    if not cadernos:
        if markdown is None:
            markdown = await _extract_text(content)
        if markdown:
            cadernos = parse_gabarito(markdown)

    if not cadernos:
        raise HTTPException(
            status_code=422,
            detail="No cadernos found in gabarito PDF. Check that the file is an ENARE gabarito.",
        )

    # Cross-validate LLM primary result against regex parser (observability only).
    # Skipped when the regex parser IS the primary result (llm_used=False).
    validation_warnings: dict[str, list[str]] = {}
    if llm_used:
        try:
            if markdown is None:
                markdown = await _extract_text(content)
            if markdown:
                regex_cadernos = parse_gabarito(markdown)
                if regex_cadernos:
                    validation_warnings = cross_validate(cadernos, regex_cadernos)
                    if validation_warnings:
                        total = sum(len(w) for w in validation_warnings.values())
                        logger.warning(
                            "cross_validate: %d discrepancy/coverage warning(s) across %d caderno(s)",
                            total,
                            len(validation_warnings),
                        )
        except Exception as exc:  # noqa: BLE001
            logger.warning("cross_validate: skipped due to error — %s", exc)

    result: list[GabaritoCaderno] = []
    for c in cadernos:
        annulled = sorted(n for n, a in c.answers.items() if a is None)
        # SECURITY: Sanitise warning strings before including them in the API
        # response.  Warnings are partially derived from LLM output (caderno
        # names).  Truncate each string and cap the list count so a corrupt or
        # adversarial LLM response cannot inflate the response body.
        raw_warnings = validation_warnings.get(c.name, [])
        safe_warnings = [
            w[:_MAX_WARNING_LEN] for w in raw_warnings[:_MAX_WARNINGS_PER_CADERNO]
        ]
        result.append(
            GabaritoCaderno(
                name=c.name,
                answers={str(k): v for k, v in sorted(c.answers.items())},
                answer_count=len(c.answers),
                annulled=annulled,
                warnings=safe_warnings,
            )
        )

    return GabaritoParseResponse(cadernos=result)


@router.post("/exams/{exam_id}/gabarito", response_model=ApplyGabaritoResponse)
async def apply_gabarito(
    exam_id: uuid.UUID,
    body: ApplyGabaritoRequest,
    session: AsyncSession = Depends(get_session),
):
    """Apply a gabarito answer map to all questions in an exam.

    Pass the ``answers`` dict from one caderno returned by POST /gabarito/parse.
    Keys are question numbers (as strings), values are A-E or null for annulled.
    Updates the ``gabarito`` field on matching Question records in-place.
    """
    exam = (await session.execute(select(Exam).where(Exam.id == exam_id))).scalar_one_or_none()
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    # Validate and convert string keys to int
    answer_map: dict[int, str | None] = {}
    for raw_key, val in body.answers.items():
        try:
            num = int(raw_key)
        except ValueError:
            raise HTTPException(
                status_code=422, detail=f"Invalid question number key: '{raw_key}'"
            )
        if val is not None and val not in "ABCDE":
            raise HTTPException(
                status_code=422, detail=f"Invalid answer '{val}' for question {num}"
            )
        answer_map[num] = val

    if not answer_map:
        raise HTTPException(status_code=422, detail="No valid answers provided")

    questions = (
        await session.execute(select(Question).where(Question.exam_id == exam_id))
    ).scalars().all()

    updated = 0
    annulled = 0
    for q in questions:
        if q.number in answer_map:
            q.gabarito = answer_map[q.number]
            updated += 1
            if answer_map[q.number] is None:
                annulled += 1

    await session.commit()
    return ApplyGabaritoResponse(updated=updated, annulled=annulled)
