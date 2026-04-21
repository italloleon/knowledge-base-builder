"""ARQ background task definitions."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from arq.connections import RedisSettings
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Exam, Job, JobStatus, ParseError, Question

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
# Main task
# ---------------------------------------------------------------------------


async def process_exam(ctx: dict, job_id: str) -> None:  # noqa: ARG001
    """Process a single exam PDF through the 3-stage pipeline."""
    logger.info("process_exam started: job_id=%s", job_id)

    async with _AsyncSession() as session:
        # 1. Load job
        result = await session.execute(select(Job).where(Job.id == uuid.UUID(job_id)))
        job = result.scalar_one_or_none()
        if not job:
            logger.error("Job %s not found in database", job_id)
            return

        # 2. Mark processing
        await _set_job_status(session, job, JobStatus.processing)

        # 3. Load exam
        exam_result = await session.execute(select(Exam).where(Exam.id == job.exam_id))
        exam = exam_result.scalar_one_or_none()
        if not exam:
            await _set_job_status(
                session, job, JobStatus.failed, "Exam record not found"
            )
            return

        # 4. Find PDF on disk
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
            # Stage 1 — extraction
            logger.info("Stage 1: extracting PDF %s", pdf_path)
            from app.pipeline.extraction import extract_markdown  # noqa: PLC0415

            try:
                markdown = extract_markdown(pdf_path)
            except Exception as exc:  # noqa: BLE001
                await _set_job_status(
                    session, job, JobStatus.failed, f"PDF extraction failed: {exc}"
                )
                return

            # Stages 2 & 3 — preprocessing + parsing
            logger.info("Stage 2/3: parsing markdown (%d chars) category=%s", len(markdown), job.category)
            from app.pipeline.parsers import get_parser  # noqa: PLC0415

            parse_result = get_parser(job.category).run(markdown)

            total_found = len(parse_result.questions) + len(parse_result.errors)

            # 5. Wipe any previous attempt's rows for this job (makes retries idempotent)
            await session.execute(delete(ParseError).where(ParseError.job_id == job.id))
            await session.execute(delete(Question).where(Question.job_id == job.id))
            await session.commit()

            # 6. Bulk insert questions
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

            # 7. Update job counts and commit parsed questions
            job.total_found = total_found
            job.parsed_ok = len(question_records)
            job.parse_errors = len(error_records)
            job.status = (
                JobStatus.completed if len(error_records) == 0 else JobStatus.partial
            )
            session.add(job)
            await session.commit()

            logger.info(
                "process_exam completed: job_id=%s total=%d ok=%d errors=%d",
                job_id,
                total_found,
                len(question_records),
                len(error_records),
            )

            # Stage 4 — LLM enrichment (optional, non-blocking on failure)
            if settings.OLLAMA_ENRICHMENT_ENABLED and question_records:
                logger.info("Stage 4: enriching %d questions via Ollama", len(question_records))
                try:
                    from app.pipeline.enrichers.ollama import enrich_questions  # noqa: PLC0415

                    # Build a number→record index for fast lookups
                    q_by_number = {qr.number: qr for qr in question_records}
                    enriched_count = 0

                    async for q_number, enrichment in enrich_questions(parse_result.questions):
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

                    logger.info(
                        "Stage 4 done: enriched %d/%d questions",
                        enriched_count,
                        len(question_records),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Stage 4 enrichment failed (non-fatal): %s", exc)

        except Exception as exc:  # noqa: BLE001
            logger.exception("process_exam crashed: job_id=%s", job_id)
            try:
                await _set_job_status(session, job, JobStatus.failed, str(exc))
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# ARQ WorkerSettings
# ---------------------------------------------------------------------------


class WorkerSettings:
    functions = [process_exam]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    max_jobs = 10
    job_timeout = 3600  # 1 hour — PDF extraction can be slow
    keep_result = 86400  # keep job results for 24 hours
