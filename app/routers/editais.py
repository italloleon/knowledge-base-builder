"""Edital query and management endpoints."""

import hashlib
import uuid
from pathlib import Path

import aiofiles
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_session
from app.models import Edital, Exam
from app.schemas import EditalEnrichResponse, EditalLinkRequest, EditalResponse, ExamResponse

router = APIRouter(tags=["editais"])

_EDITAL_NOT_FOUND = "Edital not found"
_EXAM_NOT_FOUND = "Exam not found"
_MAX_BYTES = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024


async def _save_upload(content: bytes, filename: str) -> Path:
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / filename
    async with aiofiles.open(dest, "wb") as f:
        await f.write(content)
    return dest


def _safe_filename(file_hash: str, original: str) -> str:
    return f"{file_hash[:16]}_{Path(original).name}"


@router.get("/editais", response_model=list[EditalResponse])
async def list_editais(session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(
            select(Edital)
            .options(selectinload(Edital.uploaded_by))
            .order_by(Edital.created_at.desc())
        )
    ).scalars().all()
    return rows


@router.get("/editais/{edital_id}", response_model=EditalResponse)
async def get_edital(edital_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    edital = (
        await session.execute(
            select(Edital)
            .where(Edital.id == edital_id)
            .options(selectinload(Edital.uploaded_by))
        )
    ).scalar_one_or_none()
    if not edital:
        raise HTTPException(status_code=404, detail=_EDITAL_NOT_FOUND)
    return edital


@router.delete("/editais/{edital_id}", status_code=204)
async def delete_edital(edital_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    async with session.begin():
        edital = (
            await session.execute(select(Edital).where(Edital.id == edital_id))
        ).scalar_one_or_none()
        if not edital:
            raise HTTPException(status_code=404, detail=_EDITAL_NOT_FOUND)
        await session.delete(edital)


@router.post("/editais/{edital_id}/enrich", response_model=EditalEnrichResponse, status_code=202)
async def enrich_edital_endpoint(
    edital_id: uuid.UUID, session: AsyncSession = Depends(get_session)
):
    """Queue an AI enrichment job for an existing Edital.

    Uses Gemini to extract metadata fields and knowledge areas (Anexo III)
    from the original PDF.  Returns 404 if the Edital does not exist.
    """
    edital = (
        await session.execute(select(Edital).where(Edital.id == edital_id))
    ).scalar_one_or_none()
    if not edital:
        raise HTTPException(status_code=404, detail=_EDITAL_NOT_FOUND)

    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    pool = await create_pool(redis_settings)
    job = await pool.enqueue_job("enrich_edital", str(edital_id))
    await pool.aclose()

    job_id = job.job_id if job is not None else "unknown"
    return EditalEnrichResponse(message="Enrichment queued", job_id=job_id)


@router.post("/editais/{edital_id}/enrich-upload", response_model=EditalEnrichResponse, status_code=202)
async def enrich_edital_from_upload(
    edital_id: uuid.UUID,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    """Upload an annex PDF and queue enrichment for the target edital."""
    edital = (
        await session.execute(select(Edital).where(Edital.id == edital_id))
    ).scalar_one_or_none()
    if not edital:
        raise HTTPException(status_code=404, detail=_EDITAL_NOT_FOUND)

    if file.content_type not in ("application/pdf", "application/octet-stream"):
        if not (file.filename or "").lower().endswith(".pdf"):
            raise HTTPException(status_code=422, detail="Only PDF files are accepted")

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")
    if len(content) > _MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum allowed size of {settings.MAX_UPLOAD_SIZE_MB} MB",
        )
    if not content.startswith(b"%PDF"):
        raise HTTPException(status_code=422, detail="File does not appear to be a valid PDF")

    file_hash = hashlib.sha256(content).hexdigest()
    safe_name = _safe_filename(file_hash, file.filename or "annex.pdf")
    await _save_upload(content, safe_name)

    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    pool = await create_pool(redis_settings)
    job = await pool.enqueue_job("enrich_edital", str(edital_id), file_hash)
    await pool.aclose()

    job_id = job.job_id if job is not None else "unknown"
    return EditalEnrichResponse(
        message="Annex uploaded and enrichment queued",
        job_id=job_id,
    )


@router.get("/editais/{edital_id}/exams", response_model=list[ExamResponse])
async def list_edital_exams(
    edital_id: uuid.UUID, session: AsyncSession = Depends(get_session)
):
    edital = (
        await session.execute(select(Edital).where(Edital.id == edital_id))
    ).scalar_one_or_none()
    if not edital:
        raise HTTPException(status_code=404, detail=_EDITAL_NOT_FOUND)

    from sqlalchemy import func
    from app.models import Question

    q_count_subq = (
        select(Question.exam_id, func.count(Question.id).label("question_count"))
        .group_by(Question.exam_id)
        .subquery()
    )
    enriched_count_subq = (
        select(Question.exam_id, func.count(Question.id).label("enriched_count"))
        .where(Question.enrichment.isnot(None))
        .group_by(Question.exam_id)
        .subquery()
    )

    stmt = (
        select(
            Exam,
            func.coalesce(q_count_subq.c.question_count, 0).label("question_count"),
            func.coalesce(enriched_count_subq.c.enriched_count, 0).label("enriched_count"),
        )
        .where(Exam.edital_id == edital_id)
        .outerjoin(q_count_subq, Exam.id == q_count_subq.c.exam_id)
        .outerjoin(enriched_count_subq, Exam.id == enriched_count_subq.c.exam_id)
        .options(selectinload(Exam.uploaded_by))
        .order_by(Exam.created_at.desc())
    )
    rows = (await session.execute(stmt)).all()

    return [
        ExamResponse(
            id=exam.id,
            filename=exam.filename,
            file_hash=exam.file_hash,
            edital_id=exam.edital_id,
            uploaded_by=exam.uploaded_by,
            question_count=qcount,
            enriched_count=ecount,
            created_at=exam.created_at,
        )
        for exam, qcount, ecount in rows
    ]


@router.patch("/exams/{exam_id}/edital", response_model=ExamResponse)
async def link_exam_to_edital(
    exam_id: uuid.UUID,
    body: EditalLinkRequest,
    session: AsyncSession = Depends(get_session),
):
    """Link a prova (Exam) to its Edital."""
    async with session.begin():
        exam = (
            await session.execute(select(Exam).where(Exam.id == exam_id))
        ).scalar_one_or_none()
        if not exam:
            raise HTTPException(status_code=404, detail=_EXAM_NOT_FOUND)

        edital = (
            await session.execute(select(Edital).where(Edital.id == body.edital_id))
        ).scalar_one_or_none()
        if not edital:
            raise HTTPException(status_code=404, detail=_EDITAL_NOT_FOUND)

        exam.edital_id = edital.id
        session.add(exam)

    from sqlalchemy import func
    from app.models import Question

    q_count = (
        await session.execute(
            select(func.count(Question.id)).where(Question.exam_id == exam_id)
        )
    ).scalar_one()
    enriched_count = (
        await session.execute(
            select(func.count(Question.id))
            .where(Question.exam_id == exam_id)
            .where(Question.enrichment.isnot(None))
        )
    ).scalar_one()

    # Reload with uploaded_by for the response
    exam = (
        await session.execute(
            select(Exam).where(Exam.id == exam_id).options(selectinload(Exam.uploaded_by))
        )
    ).scalar_one()

    return ExamResponse(
        id=exam.id,
        filename=exam.filename,
        file_hash=exam.file_hash,
        edital_id=exam.edital_id,
        uploaded_by=exam.uploaded_by,
        question_count=q_count,
        enriched_count=enriched_count,
        created_at=exam.created_at,
    )
