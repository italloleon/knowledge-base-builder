"""Exam and question query endpoints."""

import csv
import io
import json
import uuid
from pathlib import Path
from typing import Annotated

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.models import Exam, ParseError, Question, QuestionType, SectionType
from app.schemas import (
    EnrichRequest,
    EnrichResponse,
    ExamResponse,
    ExplainRequest,
    ExplainResponse,
    PaginatedQuestions,
    ParseErrorResponse,
    QuestionDetail,
    QuestionSummary,
)

router = APIRouter(tags=["exams"])

_EXAM_NOT_FOUND = "Exam not found"


# ---------------------------------------------------------------------------
# Exam list
# ---------------------------------------------------------------------------


@router.get("/exams", response_model=list[ExamResponse])
async def list_exams(session: AsyncSession = Depends(get_session)):
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
        .outerjoin(q_count_subq, Exam.id == q_count_subq.c.exam_id)
        .outerjoin(enriched_count_subq, Exam.id == enriched_count_subq.c.exam_id)
        .order_by(Exam.created_at.desc())
    )
    rows = (await session.execute(stmt)).all()

    result = []
    for exam, qcount, ecount in rows:
        result.append(
            ExamResponse(
                id=exam.id,
                filename=exam.filename,
                file_hash=exam.file_hash,
                edital_id=exam.edital_id,
                question_count=qcount,
                enriched_count=ecount,
                created_at=exam.created_at,
            )
        )
    return result


# ---------------------------------------------------------------------------
# Bulk export (all exams)
# ---------------------------------------------------------------------------


@router.get("/exams/export")
async def export_exams(
    format: Annotated[str, Query()] = "json",
    session: AsyncSession = Depends(get_session),
):
    """Download all exams + questions as JSON or CSV."""
    if format not in ("json", "csv"):
        raise HTTPException(status_code=422, detail="format must be 'json' or 'csv'")

    exams = (await session.execute(select(Exam).order_by(Exam.created_at))).scalars().all()

    if format == "json":
        result = []
        for exam in exams:
            qs = (
                await session.execute(
                    select(Question).where(Question.exam_id == exam.id).order_by(Question.number)
                )
            ).scalars().all()
            result.append(
                {
                    "id": str(exam.id),
                    "filename": exam.filename,
                    "file_hash": exam.file_hash,
                    "created_at": exam.created_at.isoformat(),
                    "questions": [
                        {
                            "id": str(q.id),
                            "number": q.number,
                            "section": q.section.value,
                            "question_type": q.question_type.value,
                            "enunciado": q.enunciado,
                            "items": q.items,
                            "alternatives": q.alternatives,
                            "gabarito": q.gabarito,
                            "confidence": q.confidence,
                            "enrichment": q.enrichment,
                        }
                        for q in qs
                    ],
                }
            )
        content = json.dumps(result, ensure_ascii=False, indent=2)
        return Response(
            content=content.encode("utf-8"),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="exams_export.json"'},
        )

    else:  # csv
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "exam_filename",
                "exam_id",
                "question_number",
                "section",
                "question_type",
                "enunciado",
                "alternative_a",
                "alternative_b",
                "alternative_c",
                "alternative_d",
                "alternative_e",
                "gabarito",
                "confidence",
                "area",
                "topic",
                "keywords",
                "difficulty",
                "bloom_level",
            ]
        )
        for exam in exams:
            qs = (
                await session.execute(
                    select(Question).where(Question.exam_id == exam.id).order_by(Question.number)
                )
            ).scalars().all()
            for q in qs:
                alts = q.alternatives or {}
                enr = q.enrichment or {}
                writer.writerow(
                    [
                        exam.filename,
                        str(exam.id),
                        q.number,
                        q.section.value,
                        q.question_type.value,
                        q.enunciado,
                        alts.get("A", ""),
                        alts.get("B", ""),
                        alts.get("C", ""),
                        alts.get("D", ""),
                        alts.get("E", ""),
                        q.gabarito or "",
                        q.confidence,
                        enr.get("area", ""),
                        enr.get("topic", ""),
                        ", ".join(enr.get("keywords", [])),
                        enr.get("difficulty", ""),
                        enr.get("bloom_level", ""),
                    ]
                )
        # utf-8-sig BOM so Excel auto-detects encoding
        return Response(
            content=buf.getvalue().encode("utf-8-sig"),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="exams_export.csv"'},
        )


# ---------------------------------------------------------------------------
# Enrichment trigger
# ---------------------------------------------------------------------------


@router.post("/exams/{exam_id}/enrich", response_model=EnrichResponse, status_code=202)
async def trigger_enrich(
    exam_id: uuid.UUID,
    body: EnrichRequest,
    session: AsyncSession = Depends(get_session),
):
    if body.mode not in ("missing", "all"):
        raise HTTPException(status_code=422, detail="mode must be 'missing' or 'all'")

    exam_result = await session.execute(select(Exam).where(Exam.id == exam_id))
    if not exam_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail=_EXAM_NOT_FOUND)

    q_stmt = (
        select(func.count(Question.id))
        .where(Question.exam_id == exam_id)
        .where(Question.alternatives != {})  # skip questions with no alternatives
    )
    if body.mode == "missing":
        q_stmt = q_stmt.where(Question.enrichment.is_(None))
    count = (await session.execute(q_stmt)).scalar_one()

    if count == 0:
        return EnrichResponse(message="No questions to enrich", queued=0)

    pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    await pool.enqueue_job("enrich_exam", str(exam_id), body.mode, body.provider)
    await pool.aclose()

    provider_label = (body.provider or settings.ENRICHMENT_PROVIDER).lower()
    return EnrichResponse(
        message=f"Enrichment queued ({body.mode}, {provider_label})", queued=count
    )


# ---------------------------------------------------------------------------
# Explanation enrichment trigger
# ---------------------------------------------------------------------------


@router.post("/exams/{exam_id}/explain", response_model=ExplainResponse, status_code=202)
async def trigger_explain(
    exam_id: uuid.UUID,
    body: ExplainRequest,
    session: AsyncSession = Depends(get_session),
):
    """Queue gabarito comentado generation for questions that already have a gabarito.

    Requires questions to have both a ``gabarito`` value and at least one alternative.
    Only Gemini or Ollama is used (controlled by the ``provider`` field or the server
    default ``ENRICHMENT_PROVIDER``).
    """
    if body.mode not in ("missing", "all"):
        raise HTTPException(status_code=422, detail="mode must be 'missing' or 'all'")

    exam_result = await session.execute(select(Exam).where(Exam.id == exam_id))
    if not exam_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail=_EXAM_NOT_FOUND)

    q_stmt = (
        select(func.count(Question.id))
        .where(Question.exam_id == exam_id)
        .where(Question.gabarito.isnot(None))
        .where(Question.alternatives != {})
    )
    if body.mode == "missing":
        q_stmt = q_stmt.where(Question.explanation.is_(None))
    count = (await session.execute(q_stmt)).scalar_one()

    if count == 0:
        return ExplainResponse(message="No questions to explain", queued=0)

    pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    await pool.enqueue_job("enrich_explanation", str(exam_id), body.mode, body.provider)
    await pool.aclose()

    provider_label = (body.provider or settings.ENRICHMENT_PROVIDER).lower()
    return ExplainResponse(
        message=f"Explanation enrichment queued ({body.mode}, {provider_label})", queued=count
    )


# ---------------------------------------------------------------------------
# Question list for exam
# ---------------------------------------------------------------------------


@router.get("/exams/{exam_id}/questions", response_model=PaginatedQuestions)
async def list_exam_questions(
    exam_id: uuid.UUID,
    section: Annotated[SectionType | None, Query()] = None,
    type: Annotated[QuestionType | None, Query(alias="type")] = None,
    min_confidence: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
    include_raw: Annotated[bool, Query()] = False,
    session: AsyncSession = Depends(get_session),
):
    # Verify exam exists
    exam_result = await session.execute(select(Exam).where(Exam.id == exam_id))
    if not exam_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail=_EXAM_NOT_FOUND)

    stmt = select(Question).where(Question.exam_id == exam_id)

    if section is not None:
        stmt = stmt.where(Question.section == section)
    if type is not None:
        stmt = stmt.where(Question.question_type == type)
    if min_confidence is not None:
        stmt = stmt.where(Question.confidence >= min_confidence)

    # Count total
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await session.execute(count_stmt)).scalar_one()

    # Paginate
    stmt = stmt.order_by(Question.number).offset((page - 1) * page_size).limit(page_size)
    questions = (await session.execute(stmt)).scalars().all()

    items: list[QuestionSummary] = []
    for q in questions:
        if include_raw:
            items.append(QuestionDetail.model_validate(q))
        else:
            items.append(QuestionSummary.model_validate(q))

    return PaginatedQuestions(total=total, page=page, page_size=page_size, items=items)


# ---------------------------------------------------------------------------
# Single question
# ---------------------------------------------------------------------------


@router.get("/questions/{question_id}", response_model=QuestionDetail)
async def get_question(
    question_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(Question).where(Question.id == question_id))
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    return QuestionDetail.model_validate(question)


# ---------------------------------------------------------------------------
# Parse errors for exam
# ---------------------------------------------------------------------------


@router.get("/exams/{exam_id}/errors", response_model=list[ParseErrorResponse])
async def list_exam_errors(
    exam_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    exam_result = await session.execute(select(Exam).where(Exam.id == exam_id))
    if not exam_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail=_EXAM_NOT_FOUND)

    stmt = (
        select(ParseError)
        .where(ParseError.exam_id == exam_id)
        .order_by(ParseError.created_at)
    )
    errors = (await session.execute(stmt)).scalars().all()
    return [ParseErrorResponse.model_validate(e) for e in errors]


# ---------------------------------------------------------------------------
# Delete exam
# ---------------------------------------------------------------------------


@router.delete("/exams/{exam_id}", status_code=204)
async def delete_exam(
    exam_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(Exam).where(Exam.id == exam_id))
    exam = result.scalar_one_or_none()
    if not exam:
        raise HTTPException(status_code=404, detail=_EXAM_NOT_FOUND)

    # Remove uploaded file from disk (keyed by file_hash prefix, same as worker)
    upload_dir = Path(settings.UPLOAD_DIR)
    if upload_dir.exists():
        for candidate in upload_dir.iterdir():
            if candidate.name.startswith(exam.file_hash[:16]):
                candidate.unlink(missing_ok=True)
                break

    await session.delete(exam)
    await session.commit()
    return Response(status_code=204)
