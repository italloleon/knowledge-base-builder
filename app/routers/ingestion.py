"""Ingestion endpoints — file upload and remote URL."""

import hashlib
import uuid
from pathlib import Path

import aiofiles
import httpx
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.config import settings
from app.database import get_session
from app.models import Exam, Job, JobStatus
from app.schemas import IngestResponse, IngestURLRequest

router = APIRouter(prefix="/ingest", tags=["ingestion"])

_MAX_BYTES = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024


async def _enqueue_job(job_id: str) -> None:
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    pool = await create_pool(redis_settings)
    await pool.enqueue_job("process_exam", job_id)
    await pool.aclose()


async def _get_or_create_exam(
    session: AsyncSession,
    filename: str,
    file_hash: str,
) -> Exam:
    """Return existing exam if hash matches, otherwise create a new one."""
    result = await session.execute(select(Exam).where(Exam.file_hash == file_hash))
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    exam = Exam(
        id=uuid.uuid4(),
        filename=filename,
        file_hash=file_hash,
    )
    session.add(exam)
    await session.flush()
    return exam


async def _create_job(session: AsyncSession, exam_id: uuid.UUID) -> Job:
    job = Job(
        id=uuid.uuid4(),
        exam_id=exam_id,
        status=JobStatus.pending,
    )
    session.add(job)
    await session.flush()
    return job


async def _save_upload(content: bytes, filename: str) -> Path:
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / filename
    async with aiofiles.open(dest, "wb") as f:
        await f.write(content)
    return dest


@router.post("/upload", response_model=IngestResponse, status_code=202)
async def ingest_upload(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    # Validate content type
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        # Also allow if filename ends in .pdf
        if not (file.filename or "").lower().endswith(".pdf"):
            raise HTTPException(
                status_code=422,
                detail="Only PDF files are accepted",
            )

    content = await file.read()

    if len(content) == 0:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")

    if len(content) > _MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum allowed size of {settings.MAX_UPLOAD_SIZE_MB} MB",
        )

    # Verify PDF magic bytes
    if not content.startswith(b"%PDF"):
        raise HTTPException(status_code=422, detail="File does not appear to be a valid PDF")

    file_hash = hashlib.sha256(content).hexdigest()
    safe_filename = f"{file_hash[:16]}_{Path(file.filename or 'upload').name}"

    async with session.begin():
        exam = await _get_or_create_exam(session, file.filename or "upload.pdf", file_hash)
        job = await _create_job(session, exam.id)

    await _save_upload(content, safe_filename)
    await _enqueue_job(str(job.id))

    return IngestResponse(job_id=job.id, exam_id=exam.id)


@router.post("/url", response_model=IngestResponse, status_code=202)
async def ingest_url(
    body: IngestURLRequest,
    session: AsyncSession = Depends(get_session),
):
    url = body.url

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,
                read=120.0,
                write=10.0,
                pool=5.0,
            ),
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            content = response.content
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Remote server returned HTTP {exc.response.status_code}",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to download PDF from URL: {exc}",
        ) from exc

    if len(content) == 0:
        raise HTTPException(status_code=422, detail="Downloaded file is empty")

    if len(content) > _MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum allowed size of {settings.MAX_UPLOAD_SIZE_MB} MB",
        )

    if not content.startswith(b"%PDF"):
        raise HTTPException(
            status_code=422, detail="Downloaded content does not appear to be a valid PDF"
        )

    # Derive filename from URL path
    url_path = url.split("?")[0]
    original_filename = url_path.split("/")[-1] or "remote.pdf"
    if not original_filename.lower().endswith(".pdf"):
        original_filename += ".pdf"

    file_hash = hashlib.sha256(content).hexdigest()
    safe_filename = f"{file_hash[:16]}_{original_filename}"

    async with session.begin():
        exam = await _get_or_create_exam(session, original_filename, file_hash)
        job = await _create_job(session, exam.id)

    await _save_upload(content, safe_filename)
    await _enqueue_job(str(job.id))

    return IngestResponse(job_id=job.id, exam_id=exam.id)
