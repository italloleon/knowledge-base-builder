"""ARQ background task definitions."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from arq.connections import RedisSettings
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models import DocumentCategory, Edital, Exam, Job, JobStatus, ParseError, Question

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    force=True,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Database setup for worker process
# ---------------------------------------------------------------------------

_engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
_AsyncSession = async_sessionmaker(
    bind=_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _set_job_status(
    session: AsyncSession,
    job: Job,
    status: JobStatus,
    error_message: str | None = None,
) -> None:
    job.status = status
    if error_message is not None:
        job.error_message = error_message
    session.add(job)
    await session.commit()


def _find_pdf_by_hash(file_hash: str) -> Path | None:
    """Locate the saved PDF in UPLOAD_DIR by its hash prefix."""
    upload_dir = Path(settings.UPLOAD_DIR)
    if not upload_dir.exists():
        return None
    for candidate in upload_dir.iterdir():
        if candidate.name.startswith(file_hash[:16]):
            return candidate
    return None


# ---------------------------------------------------------------------------
# Edital processing path
# ---------------------------------------------------------------------------


async def _process_edital_job(session: AsyncSession, job: Job) -> None:
    """Extract structured data from an Edital PDF and persist to the editais table."""
    edital_result = await session.execute(select(Edital).where(Edital.id == job.edital_id))
    edital = edital_result.scalar_one_or_none()
    if not edital:
        await _set_job_status(session, job, JobStatus.failed, "Edital record not found")
        return

    pdf_path = _find_pdf_by_hash(edital.file_hash)
    if not pdf_path or not pdf_path.exists():
        await _set_job_status(
            session,
            job,
            JobStatus.failed,
            f"PDF file not found on disk for hash {edital.file_hash[:16]}",
        )
        return

    try:
        logger.info("Stage 1 (edital): extracting PDF %s", pdf_path)
        from app.pipeline.extraction import extract_markdown  # noqa: PLC0415

        try:
            markdown = extract_markdown(pdf_path)
        except Exception as exc:  # noqa: BLE001
            await _set_job_status(session, job, JobStatus.failed, f"PDF extraction failed: {exc}")
            return

        logger.info("Stage 2/3 (edital): parsing markdown (%d chars)", len(markdown))
        from app.pipeline.parsers.edital import EditalParser  # noqa: PLC0415

        data = EditalParser().extract_edital(markdown)

        # Persist extracted fields onto the Edital record
        edital.numero_edital = data.numero_edital
        edital.ano = data.ano
        edital.edition_name = data.edition_name
        edital.organizadora = data.organizadora
        edital.instituicao_gestora = data.instituicao_gestora
        edital.modalidade = data.modalidade
        edital.total_questoes_gerais = data.total_questoes_gerais
        edital.total_questoes_especificas = data.total_questoes_especificas
        edital.percentual_minimo_aprovacao = data.percentual_minimo_aprovacao
        edital.bolsa_mensal = data.bolsa_mensal
        edital.data_inicio_programas = data.data_inicio_programas
        edital.contato_email = data.contato_email
        edital.contato_telefone = data.contato_telefone
        edital.url_enare = data.url_enare
        if data.cronograma:
            edital.cronograma = data.cronograma
        if data.vagas:
            edital.vagas = data.vagas
        if data.instituicoes:
            edital.instituicoes = data.instituicoes
        if data.knowledge_areas:
            edital.knowledge_areas = data.knowledge_areas

        session.add(edital)

        job.total_found = 1
        job.parsed_ok = 1
        job.parse_errors = 0
        job.status = JobStatus.completed
        session.add(job)

        await session.commit()
        logger.info("process_document (edital) completed: job_id=%s edital_id=%s", job.id, edital.id)

    except Exception as exc:  # noqa: BLE001
        logger.exception("process_document (edital) crashed: job_id=%s", job.id)
        try:
            await _set_job_status(session, job, JobStatus.failed, str(exc))
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Main task (dispatches by category)
# ---------------------------------------------------------------------------


async def process_document(ctx: dict, job_id: str) -> None:  # noqa: ARG001
    """Process a single document PDF through the pipeline (prova or edital)."""
    logger.info("process_document started: job_id=%s", job_id)

    async with _AsyncSession() as session:
        result = await session.execute(select(Job).where(Job.id == uuid.UUID(job_id)))
        job = result.scalar_one_or_none()
        if not job:
            logger.error("Job %s not found in database", job_id)
            return

        await _set_job_status(session, job, JobStatus.processing)

        if job.category == DocumentCategory.edital:
            await _process_edital_job(session, job)
            return

        # --- prova path ---

        exam_result = await session.execute(select(Exam).where(Exam.id == job.exam_id))
        exam = exam_result.scalar_one_or_none()
        if not exam:
            await _set_job_status(session, job, JobStatus.failed, "Exam record not found")
            return

        pdf_path = _find_pdf_by_hash(exam.file_hash)
        if not pdf_path or not pdf_path.exists():
            await _set_job_status(
                session,
                job,
                JobStatus.failed,
                f"PDF file not found on disk for hash {exam.file_hash[:16]}",
            )
            return

        try:
            logger.info("Stage 1: extracting PDF %s", pdf_path)
            from app.pipeline.extraction import extract_markdown  # noqa: PLC0415

            try:
                markdown = extract_markdown(pdf_path)
            except Exception as exc:  # noqa: BLE001
                await _set_job_status(
                    session, job, JobStatus.failed, f"PDF extraction failed: {exc}"
                )
                return

            logger.info(
                "Stage 2/3: parsing markdown (%d chars) category=%s", len(markdown), job.category
            )
            from app.pipeline.parsers import get_parser  # noqa: PLC0415

            parse_result = get_parser(job.category).run(markdown)

            total_found = len(parse_result.questions) + len(parse_result.errors)

            await session.execute(delete(ParseError).where(ParseError.exam_id == exam.id))
            await session.execute(delete(Question).where(Question.exam_id == exam.id))
            await session.commit()

            question_records: list[Question] = []
            for pq in parse_result.questions:
                question_records.append(
                    Question(
                        id=uuid.uuid4(),
                        exam_id=exam.id,
                        job_id=job.id,
                        number=pq.number,
                        section=pq.section,
                        question_type=pq.question_type,
                        enunciado=pq.enunciado,
                        items=pq.items,
                        alternatives=pq.alternatives,
                        gabarito=None,
                        raw_block=pq.raw_block,
                        confidence=pq.confidence,
                    )
                )

            error_records: list[ParseError] = []
            for pf in parse_result.errors:
                error_records.append(
                    ParseError(
                        id=uuid.uuid4(),
                        exam_id=exam.id,
                        job_id=job.id,
                        raw_block=pf.raw_block,
                        reason=pf.reason,
                    )
                )

            session.add_all(question_records)
            session.add_all(error_records)

            job.total_found = total_found
            job.parsed_ok = len(question_records)
            job.parse_errors = len(error_records)
            job.status = (
                JobStatus.completed if len(error_records) == 0 else JobStatus.partial
            )
            session.add(job)
            await session.commit()

            logger.info(
                "process_document (prova) completed: job_id=%s total=%d ok=%d errors=%d",
                job_id,
                total_found,
                len(question_records),
                len(error_records),
            )

        except Exception as exc:  # noqa: BLE001
            logger.exception("process_document (prova) crashed: job_id=%s", job_id)
            try:
                await _set_job_status(session, job, JobStatus.failed, str(exc))
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Enrichment-only task
# ---------------------------------------------------------------------------


async def enrich_exam(  # noqa: ARG001
    ctx: dict, exam_id: str, mode: str, provider: str | None = None
) -> None:
    """Enrich questions for an existing exam. mode='missing'|'all', provider='ollama'|'gemini'."""
    resolved_provider = (provider or settings.ENRICHMENT_PROVIDER).lower()
    logger.info(
        "enrich_exam started: exam_id=%s mode=%s provider=%s", exam_id, mode, resolved_provider
    )

    from app.pipeline.base import ParsedQuestion  # noqa: PLC0415
    from app.pipeline.enrichers import get_enricher  # noqa: PLC0415
    from app.pipeline.enrichers.taxonomy import build_taxonomy_context  # noqa: PLC0415

    enrich_questions = get_enricher(resolved_provider)

    async with _AsyncSession() as session:
        exam_result = await session.execute(select(Exam).where(Exam.id == uuid.UUID(exam_id)))
        exam = exam_result.scalar_one_or_none()
        if exam is None:
            logger.warning("enrich_exam: exam not found (%s)", exam_id)
            return

        taxonomy_context: dict | None = None
        if exam.edital_id is not None:
            edital_result = await session.execute(select(Edital).where(Edital.id == exam.edital_id))
            edital = edital_result.scalar_one_or_none()
            if edital and edital.knowledge_areas:
                taxonomy_context = build_taxonomy_context(
                    edital.knowledge_areas,
                    edital_id=str(edital.id),
                )
                if taxonomy_context:
                    logger.info(
                        "enrich_exam taxonomy mode ENABLED: exam_id=%s edital_id=%s areas=%d",
                        exam_id,
                        edital.id,
                        len(taxonomy_context.get("areas", [])),
                    )
                else:
                    logger.info(
                        "enrich_exam taxonomy mode DISABLED: edital_id=%s has no usable areas",
                        edital.id,
                    )
            else:
                logger.info(
                    "enrich_exam taxonomy mode DISABLED: exam_id=%s edital_id=%s without knowledge_areas",
                    exam_id,
                    exam.edital_id,
                )
        else:
            logger.info(
                "enrich_exam taxonomy mode DISABLED: exam_id=%s has no linked edital",
                exam_id,
            )

        stmt = (
            select(Question)
            .where(Question.exam_id == uuid.UUID(exam_id))
            .where(Question.alternatives != {})
        )
        if mode == "missing":
            stmt = stmt.where(Question.enrichment.is_(None))
        stmt = stmt.order_by(Question.number)

        questions = (await session.execute(stmt)).scalars().all()

        if not questions:
            logger.info("enrich_exam: nothing to enrich for exam %s", exam_id)
            return

        logger.info("enrich_exam: enriching %d questions", len(questions))

        parsed = [
            ParsedQuestion(
                number=q.number,
                section=q.section.value if hasattr(q.section, "value") else str(q.section),
                question_type=q.question_type.value if hasattr(q.question_type, "value") else str(q.question_type),
                enunciado=q.enunciado,
                items=q.items,
                alternatives=q.alternatives or {},
                gabarito=q.gabarito,
                raw_block="",
                confidence=q.confidence,
            )
            for q in questions
        ]
        q_by_number = {q.number: q for q in questions}
        enriched_count = 0

        try:
            async for q_number, enrichment in enrich_questions(
                parsed,
                taxonomy_context=taxonomy_context,
            ):
                if enrichment is not None:
                    q_record = q_by_number.get(q_number)
                    if q_record is not None:
                        await session.execute(
                            update(Question)
                            .where(Question.id == q_record.id)
                            .values(enrichment=enrichment)
                        )
                        await session.commit()
                        enriched_count += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("enrich_exam failed (partial): %s", exc)

        logger.info(
            "enrich_exam done: exam_id=%s enriched=%d/%d",
            exam_id,
            enriched_count,
            len(questions),
        )


# ---------------------------------------------------------------------------
# Edital enrichment task
# ---------------------------------------------------------------------------


async def enrich_edital(  # noqa: ARG001
    ctx: dict, edital_id: str, source_file_hash: str | None = None
) -> None:
    """Enrich an existing Edital record using Gemini AI.

    Runs Docling PDF extraction in a thread executor (blocking call), then
    sends the full markdown to Gemini for metadata and knowledge-area extraction.

    Merge strategy:
    - Scalar fields: only overwrite when DB value is None and Gemini found something
    - JSONB lists (knowledge_areas, cronograma, vagas, instituicoes): always
      overwrite when Gemini returned non-empty data — Gemini has the full picture
    """
    import asyncio  # noqa: PLC0415
    from functools import partial  # noqa: PLC0415

    logger.info(
        "enrich_edital started: edital_id=%s source_file_hash=%s",
        edital_id,
        source_file_hash[:16] if source_file_hash else "<edital.file_hash>",
    )

    async with _AsyncSession() as session:
        edital_result = await session.execute(
            select(Edital).where(Edital.id == uuid.UUID(edital_id))
        )
        edital = edital_result.scalar_one_or_none()
        if not edital:
            logger.error("enrich_edital: Edital %s not found", edital_id)
            return

        source_hash = source_file_hash or edital.file_hash
        pdf_path = _find_pdf_by_hash(source_hash)
        if not pdf_path or not pdf_path.exists():
            logger.error(
                "enrich_edital: PDF not found on disk for hash %s",
                source_hash[:16],
            )
            return

        # --- Stage 1: Docling extraction (blocking — run in thread) ---
        try:
            from app.pipeline.extraction import extract_markdown  # noqa: PLC0415

            logger.info("enrich_edital: extracting PDF %s", pdf_path)
            loop = asyncio.get_event_loop()
            markdown = await loop.run_in_executor(None, partial(extract_markdown, pdf_path))
        except Exception as exc:  # noqa: BLE001
            logger.error("enrich_edital: PDF extraction failed: %s", exc)
            return

        logger.info("enrich_edital: extracted %d chars of markdown", len(markdown))

        # --- Stage 2: Gemini enrichment ---
        from app.pipeline.enrichers.edital_gemini import enrich_edital as _gemini_enrich  # noqa: PLC0415

        data = await _gemini_enrich(markdown)

        # --- Stage 3: Merge into DB record ---
        # Scalar fields: only fill in gaps (don't overwrite existing data)
        if edital.numero_edital is None and data.numero_edital is not None:
            edital.numero_edital = data.numero_edital
        if edital.ano is None and data.ano is not None:
            edital.ano = data.ano
        if edital.edition_name is None and data.edition_name is not None:
            edital.edition_name = data.edition_name
        if edital.organizadora is None and data.organizadora is not None:
            edital.organizadora = data.organizadora
        if edital.instituicao_gestora is None and data.instituicao_gestora is not None:
            edital.instituicao_gestora = data.instituicao_gestora
        if edital.modalidade is None and data.modalidade is not None:
            edital.modalidade = data.modalidade
        if edital.total_questoes_gerais is None and data.total_questoes_gerais is not None:
            edital.total_questoes_gerais = data.total_questoes_gerais
        if edital.total_questoes_especificas is None and data.total_questoes_especificas is not None:
            edital.total_questoes_especificas = data.total_questoes_especificas
        if edital.percentual_minimo_aprovacao is None and data.percentual_minimo_aprovacao is not None:
            edital.percentual_minimo_aprovacao = data.percentual_minimo_aprovacao
        if edital.bolsa_mensal is None and data.bolsa_mensal is not None:
            edital.bolsa_mensal = data.bolsa_mensal
        if edital.data_inicio_programas is None and data.data_inicio_programas is not None:
            edital.data_inicio_programas = data.data_inicio_programas
        if edital.contato_email is None and data.contato_email is not None:
            edital.contato_email = data.contato_email
        if edital.contato_telefone is None and data.contato_telefone is not None:
            edital.contato_telefone = data.contato_telefone
        if edital.url_enare is None and data.url_enare is not None:
            edital.url_enare = data.url_enare

        # JSONB lists: always overwrite when Gemini returned non-empty data
        if data.knowledge_areas:
            edital.knowledge_areas = data.knowledge_areas
        if data.cronograma:
            edital.cronograma = data.cronograma
        if data.vagas:
            edital.vagas = data.vagas
        if data.instituicoes:
            edital.instituicoes = data.instituicoes

        session.add(edital)
        await session.commit()

        logger.info(
            "enrich_edital completed: edital_id=%s knowledge_areas=%d cronograma=%d vagas=%d",
            edital_id,
            len(edital.knowledge_areas or []),
            len(edital.cronograma or []),
            len(edital.vagas or []),
        )


# ---------------------------------------------------------------------------
# Explanation enrichment task
# ---------------------------------------------------------------------------


async def enrich_explanation(  # noqa: ARG001
    ctx: dict, exam_id: str, mode: str, provider: str | None = None
) -> None:
    """Generate gabarito comentado for questions that already have a gabarito.

    mode='missing': only questions where explanation IS NULL
    mode='all':     all questions with a gabarito
    provider='ollama'|'gemini': overrides ENRICHMENT_PROVIDER
    """
    resolved_provider = (provider or settings.ENRICHMENT_PROVIDER).lower()
    logger.info(
        "enrich_explanation started: exam_id=%s mode=%s provider=%s",
        exam_id,
        mode,
        resolved_provider,
    )

    from app.pipeline.base import ParsedQuestion  # noqa: PLC0415

    if resolved_provider == "gemini":
        from app.pipeline.enrichers.explanation_gemini import generate_explanations  # noqa: PLC0415
    else:
        from app.pipeline.enrichers.explanation_ollama import generate_explanations  # noqa: PLC0415

    async with _AsyncSession() as session:
        stmt = (
            select(Question)
            .where(Question.exam_id == uuid.UUID(exam_id))
            .where(Question.gabarito.isnot(None))
            .where(Question.alternatives != {})
        )
        if mode == "missing":
            stmt = stmt.where(Question.explanation.is_(None))
        stmt = stmt.order_by(Question.number)

        questions = (await session.execute(stmt)).scalars().all()

        if not questions:
            logger.info("enrich_explanation: nothing to explain for exam %s", exam_id)
            return

        logger.info("enrich_explanation: explaining %d questions", len(questions))

        parsed = [
            ParsedQuestion(
                number=q.number,
                section=q.section.value if hasattr(q.section, "value") else str(q.section),
                question_type=q.question_type.value if hasattr(q.question_type, "value") else str(q.question_type),
                enunciado=q.enunciado,
                items=q.items,
                alternatives=q.alternatives or {},
                gabarito=q.gabarito,
                raw_block="",
                confidence=q.confidence,
            )
            for q in questions
        ]
        gabaritos = {q.number: q.gabarito for q in questions}  # type: ignore[misc]
        enrichments = {q.number: q.enrichment for q in questions}
        q_by_number = {q.number: q for q in questions}
        explained_count = 0

        try:
            async for q_number, explanation in generate_explanations(
                parsed,
                gabaritos=gabaritos,
                enrichments=enrichments,
            ):
                if explanation is not None:
                    q_record = q_by_number.get(q_number)
                    if q_record is not None:
                        await session.execute(
                            update(Question)
                            .where(Question.id == q_record.id)
                            .values(
                                explanation=explanation,
                                explanation_flagged=explanation.get("flagged", False),
                            )
                        )
                        await session.commit()
                        explained_count += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("enrich_explanation failed (partial): %s", exc)

        logger.info(
            "enrich_explanation done: exam_id=%s explained=%d/%d",
            exam_id,
            explained_count,
            len(questions),
        )


# ---------------------------------------------------------------------------
# ARQ WorkerSettings
# ---------------------------------------------------------------------------


class WorkerSettings:
    functions = [process_document, enrich_exam, enrich_edital, enrich_explanation]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    max_jobs = 10
    job_timeout = 3600
    keep_result = 86400
