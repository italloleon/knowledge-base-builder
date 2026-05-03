"""Import/export endpoints for knowledge-base datasets."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.database import get_session
from app.models import (
    DocumentCategory,
    Edital,
    Exam,
    Job,
    JobStatus,
    ParseError,
    Question,
    QuestionType,
    SectionType,
)
from app.schemas import FullImportResponse, ImportResponse

router = APIRouter(tags=["import"])

_MAX_IMPORT_BYTES = 50 * 1024 * 1024  # 50 MB
_MAX_EXAMS_PER_IMPORT = 100
_MAX_QUESTIONS_PER_EXAM = 500
_MAX_EDITAIS_PER_IMPORT = 200


def _json_load_or_422(raw: bytes):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid JSON: {exc}") from exc


def _parse_section(value: str) -> SectionType:
    try:
        return SectionType(value)
    except ValueError:
        return SectionType.unknown


def _parse_question_type(value: str) -> QuestionType:
    try:
        return QuestionType(value)
    except ValueError:
        return QuestionType.unknown


async def _find_existing_question(
    session: AsyncSession, exam_id: uuid.UUID, enunciado: str, alternatives: dict
) -> Question | None:
    return (
        await session.execute(
            select(Question).where(
                Question.exam_id == exam_id,
                Question.enunciado == enunciado,
                Question.alternatives == alternatives,
            ).limit(1)
        )
    ).scalars().first()


@router.post("/import", response_model=ImportResponse)
async def import_exams(
    request: Request,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    """Import exams from a legacy JSON export file (array of exams)."""
    content_length = int(request.headers.get("content-length", 0))
    if content_length > _MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="Import file too large (max 50 MB)")

    raw = await file.read(_MAX_IMPORT_BYTES + 1)
    if len(raw) > _MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="Import file too large (max 50 MB)")

    data = _json_load_or_422(raw)
    if not isinstance(data, list):
        raise HTTPException(
            status_code=422, detail="Expected a JSON array of exams at the top level"
        )
    if len(data) > _MAX_EXAMS_PER_IMPORT:
        raise HTTPException(
            status_code=422, detail=f"Too many exams in import (max {_MAX_EXAMS_PER_IMPORT})"
        )

    exams_created = 0
    exams_existing = 0
    questions_created = 0
    questions_skipped = 0
    questions_enrichment_updated = 0

    for exam_data in data:
        if not isinstance(exam_data, dict):
            continue
        file_hash = exam_data.get("file_hash") or ""
        filename = exam_data.get("filename") or "imported.pdf"
        raw_questions = (exam_data.get("questions") or [])[:_MAX_QUESTIONS_PER_EXAM]

        existing = None
        if file_hash:
            existing = (
                await session.execute(select(Exam).where(Exam.file_hash == file_hash))
            ).scalar_one_or_none()
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
            enunciado = q_data.get("enunciado") or ""
            alternatives = q_data.get("alternatives") or {}
            if not enunciado:
                continue

            existing_q = await _find_existing_question(session, exam.id, enunciado, alternatives)
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

            session.add(
                Question(
                    id=uuid.uuid4(),
                    exam_id=exam.id,
                    job_id=import_job.id,
                    number=q_data.get("number") or 0,
                    section=_parse_section(q_data.get("section", "unknown")),
                    question_type=_parse_question_type(q_data.get("question_type", "unknown")),
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


@router.get("/import/export/full")
async def export_full(session: AsyncSession = Depends(get_session)):
    """Export all editais, exams, questions, and parse errors as a single JSON."""
    editais = (await session.execute(select(Edital).order_by(Edital.created_at))).scalars().all()
    exams = (await session.execute(select(Exam).order_by(Exam.created_at))).scalars().all()
    parse_errors = (
        await session.execute(select(ParseError).order_by(ParseError.created_at))
    ).scalars().all()

    exam_questions: dict[uuid.UUID, list[Question]] = {}
    for exam in exams:
        exam_questions[exam.id] = (
            await session.execute(
                select(Question).where(Question.exam_id == exam.id).order_by(Question.number)
            )
        ).scalars().all()

    payload = {
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "editais": [
            {
                "id": str(e.id),
                "filename": e.filename,
                "file_hash": e.file_hash,
                "numero_edital": e.numero_edital,
                "ano": e.ano,
                "edition_name": e.edition_name,
                "organizadora": e.organizadora,
                "instituicao_gestora": e.instituicao_gestora,
                "modalidade": e.modalidade,
                "total_questoes_gerais": e.total_questoes_gerais,
                "total_questoes_especificas": e.total_questoes_especificas,
                "percentual_minimo_aprovacao": e.percentual_minimo_aprovacao,
                "bolsa_mensal": e.bolsa_mensal,
                "data_inicio_programas": e.data_inicio_programas,
                "contato_email": e.contato_email,
                "contato_telefone": e.contato_telefone,
                "url_enare": e.url_enare,
                "cronograma": e.cronograma,
                "vagas": e.vagas,
                "instituicoes": e.instituicoes,
                "knowledge_areas": e.knowledge_areas,
                "created_at": e.created_at.isoformat(),
            }
            for e in editais
        ],
        "exams": [
            {
                "id": str(exam.id),
                "filename": exam.filename,
                "file_hash": exam.file_hash,
                "edital_id": str(exam.edital_id) if exam.edital_id else None,
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
                    for q in exam_questions[exam.id]
                ],
            }
            for exam in exams
        ],
        "parse_errors": [
            {
                "id": str(err.id),
                "exam_id": str(err.exam_id),
                "reason": err.reason,
                "raw_block": err.raw_block,
                "created_at": err.created_at.isoformat(),
            }
            for err in parse_errors
        ],
    }

    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="knowledge_base_full_export.json"'},
    )


@router.post("/import/full", response_model=FullImportResponse)
async def import_full(
    request: Request,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    """Import full export JSON preserving edital->exam->question relationships."""
    content_length = int(request.headers.get("content-length", 0))
    if content_length > _MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="Import file too large (max 50 MB)")

    raw = await file.read(_MAX_IMPORT_BYTES + 1)
    if len(raw) > _MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="Import file too large (max 50 MB)")

    data = _json_load_or_422(raw)
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="Expected a JSON object at top level")

    editais_data = data.get("editais") or []
    exams_data = data.get("exams") or []
    if not isinstance(editais_data, list) or not isinstance(exams_data, list):
        raise HTTPException(
            status_code=422, detail="Expected 'editais' and 'exams' arrays in full import file"
        )
    if len(editais_data) > _MAX_EDITAIS_PER_IMPORT:
        raise HTTPException(
            status_code=422, detail=f"Too many editais in import (max {_MAX_EDITAIS_PER_IMPORT})"
        )
    if len(exams_data) > _MAX_EXAMS_PER_IMPORT:
        raise HTTPException(
            status_code=422, detail=f"Too many exams in import (max {_MAX_EXAMS_PER_IMPORT})"
        )

    editais_created = 0
    editais_existing = 0
    exams_created = 0
    exams_existing = 0
    questions_created = 0
    questions_skipped = 0
    questions_enrichment_updated = 0
    source_edital_to_target: dict[str, uuid.UUID] = {}

    for edital_data in editais_data:
        if not isinstance(edital_data, dict):
            continue
        file_hash = (edital_data.get("file_hash") or "").strip()
        if not file_hash:
            continue
        existing = (
            await session.execute(select(Edital).where(Edital.file_hash == file_hash))
        ).scalar_one_or_none()
        if existing:
            edital = existing
            editais_existing += 1
        else:
            edital = Edital(
                id=uuid.uuid4(),
                filename=edital_data.get("filename") or "imported_edital.pdf",
                file_hash=file_hash,
            )
            session.add(edital)
            await session.flush()
            editais_created += 1

        edital.numero_edital = edital_data.get("numero_edital")
        edital.ano = edital_data.get("ano")
        edital.edition_name = edital_data.get("edition_name")
        edital.organizadora = edital_data.get("organizadora")
        edital.instituicao_gestora = edital_data.get("instituicao_gestora")
        edital.modalidade = edital_data.get("modalidade")
        edital.total_questoes_gerais = edital_data.get("total_questoes_gerais")
        edital.total_questoes_especificas = edital_data.get("total_questoes_especificas")
        edital.percentual_minimo_aprovacao = edital_data.get("percentual_minimo_aprovacao")
        edital.bolsa_mensal = edital_data.get("bolsa_mensal")
        edital.data_inicio_programas = edital_data.get("data_inicio_programas")
        edital.contato_email = edital_data.get("contato_email")
        edital.contato_telefone = edital_data.get("contato_telefone")
        edital.url_enare = edital_data.get("url_enare")
        edital.cronograma = edital_data.get("cronograma")
        edital.vagas = edital_data.get("vagas")
        edital.instituicoes = edital_data.get("instituicoes")
        edital.knowledge_areas = edital_data.get("knowledge_areas")
        session.add(edital)

        source_id = str(edital_data.get("id") or "")
        if source_id:
            source_edital_to_target[source_id] = edital.id

    await session.flush()

    for exam_data in exams_data:
        if not isinstance(exam_data, dict):
            continue
        file_hash = (exam_data.get("file_hash") or "").strip()
        filename = exam_data.get("filename") or "imported_exam.pdf"
        raw_questions = (exam_data.get("questions") or [])[:_MAX_QUESTIONS_PER_EXAM]

        existing_exam = None
        if file_hash:
            existing_exam = (
                await session.execute(select(Exam).where(Exam.file_hash == file_hash))
            ).scalar_one_or_none()
        if existing_exam:
            exam = existing_exam
            exams_existing += 1
        else:
            if not file_hash:
                file_hash = uuid.uuid4().hex
            exam = Exam(id=uuid.uuid4(), filename=filename, file_hash=file_hash)
            session.add(exam)
            await session.flush()
            exams_created += 1

        source_edital_id = exam_data.get("edital_id")
        if source_edital_id:
            exam.edital_id = source_edital_to_target.get(str(source_edital_id))
        else:
            exam.edital_id = None
        exam.filename = filename
        session.add(exam)
        await session.flush()

        import_job = Job(
            id=uuid.uuid4(),
            exam_id=exam.id,
            edital_id=exam.edital_id,
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
            enunciado = q_data.get("enunciado") or ""
            alternatives = q_data.get("alternatives") or {}
            if not enunciado:
                continue

            existing_q = await _find_existing_question(session, exam.id, enunciado, alternatives)
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

            session.add(
                Question(
                    id=uuid.uuid4(),
                    exam_id=exam.id,
                    job_id=import_job.id,
                    number=q_data.get("number") or 0,
                    section=_parse_section(q_data.get("section", "unknown")),
                    question_type=_parse_question_type(q_data.get("question_type", "unknown")),
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

    return FullImportResponse(
        editais_created=editais_created,
        editais_existing=editais_existing,
        exams_created=exams_created,
        exams_existing=exams_existing,
        questions_created=questions_created,
        questions_skipped=questions_skipped,
        questions_enrichment_updated=questions_enrichment_updated,
    )
