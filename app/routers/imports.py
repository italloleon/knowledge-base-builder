"""JSON import endpoint — restores an exported knowledge base dump."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.database import get_session
from app.models import DocumentCategory, Exam, Job, JobStatus, Question, QuestionType, SectionType
from app.schemas import ImportResponse

router = APIRouter(tags=["import"])

_MAX_IMPORT_BYTES = 50 * 1024 * 1024  # 50 MB
_MAX_EXAMS_PER_IMPORT = 100
_MAX_QUESTIONS_PER_EXAM = 500


@router.post("/import", response_model=ImportResponse)
async def import_exams(
    request: Request,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    """Import exams from a JSON export file.

    Deduplicates questions by (enunciado + alternatives) within the same exam.
    For duplicates already existing without enrichment, copies enrichment from the import.
    """
    content_length = int(request.headers.get("content-length", 0))
    if content_length > _MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="Import file too large (max 50 MB)")

    raw = await file.read(_MAX_IMPORT_BYTES + 1)
    if len(raw) > _MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="Import file too large (max 50 MB)")

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid JSON: {exc}") from exc

    if not isinstance(data, list):
        raise HTTPException(
            status_code=422, detail="Expected a JSON array of exams at the top level"
        )

    if len(data) > _MAX_EXAMS_PER_IMPORT:
        raise HTTPException(status_code=422, detail=f"Too many exams in import (max {_MAX_EXAMS_PER_IMPORT})")

    exams_created = 0
    exams_existing = 0
    questions_created = 0
    questions_skipped = 0
    questions_enrichment_updated = 0

    for exam_data in data:
        if not isinstance(exam_data, dict):
            continue

        file_hash: str = exam_data.get("file_hash") or ""
        filename: str = exam_data.get("filename") or "imported.pdf"
        raw_questions: list = (exam_data.get("questions") or [])[:_MAX_QUESTIONS_PER_EXAM]

        # Find or create exam by file_hash
        exam: Exam
        if file_hash:
            existing = (
                await session.execute(select(Exam).where(Exam.file_hash == file_hash))
            ).scalar_one_or_none()
        else:
            existing = None

        if existing:
            exam = existing
            exams_existing += 1
        else:
            if not file_hash:
                file_hash = uuid.uuid4().hex
            exam = Exam(id=uuid.uuid4(), filename=filename, file_hash=file_hash)
            session.add(exam)
            await session.flush()
            exams_created += 1

        # Synthetic job record to satisfy Question.job_id FK
        import_job = Job(
            id=uuid.uuid4(),
            exam_id=exam.id,
            category=DocumentCategory.prova,
            status=JobStatus.completed,
            total_found=len(raw_questions),
            parsed_ok=0,
            parse_errors=0,
        )
        session.add(import_job)
        await session.flush()

        job_created = 0

        for q_data in raw_questions:
            if not isinstance(q_data, dict):
                continue

            enunciado: str = q_data.get("enunciado") or ""
            alternatives: dict = q_data.get("alternatives") or {}

            if not enunciado:
                continue

            # Deduplicate within this exam only — questions can legitimately repeat
            # across different exams (ENARE reuses questions), so we scope the
            # check to exam_id to avoid false-positive skips on cross-exam matches.
            existing_q = (
                await session.execute(
                    select(Question).where(
                        Question.exam_id == exam.id,
                        Question.enunciado == enunciado,
                        Question.alternatives == alternatives,
                    ).limit(1)
                )
            ).scalars().first()

            if existing_q is not None:
                imported_enrichment = q_data.get("enrichment")
                if imported_enrichment and existing_q.enrichment is None:
                    await session.execute(
                        update(Question)
                        .where(Question.id == existing_q.id)
                        .values(enrichment=imported_enrichment)
                    )
                    questions_enrichment_updated += 1
                else:
                    questions_skipped += 1
                continue

            try:
                section = SectionType(q_data.get("section", "unknown"))
            except ValueError:
                section = SectionType.unknown

            try:
                question_type = QuestionType(q_data.get("question_type", "unknown"))
            except ValueError:
                question_type = QuestionType.unknown

            session.add(
                Question(
                    id=uuid.uuid4(),
                    exam_id=exam.id,
                    job_id=import_job.id,
                    number=q_data.get("number") or 0,
                    section=section,
                    question_type=question_type,
                    enunciado=enunciado,
                    items=q_data.get("items"),
                    alternatives=alternatives,
                    gabarito=q_data.get("gabarito"),
                    raw_block="",
                    confidence=q_data.get("confidence") or 1.0,
                    enrichment=q_data.get("enrichment"),
                )
            )
            questions_created += 1
            job_created += 1

        import_job.parsed_ok = job_created
        session.add(import_job)
        await session.commit()

    return ImportResponse(
        exams_created=exams_created,
        exams_existing=exams_existing,
        questions_created=questions_created,
        questions_skipped=questions_skipped,
        questions_enrichment_updated=questions_enrichment_updated,
    )
