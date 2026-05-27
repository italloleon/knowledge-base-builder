"""Ingestion endpoints — file upload and remote URL."""

import hashlib
import ipaddress
import socket
import uuid
from pathlib import Path
from urllib.parse import urlparse

import aiofiles
import httpx
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.auth.deps import get_current_user
from app.config import settings
from app.database import get_session
from app.models import DocumentCategory, Edital, Exam, Job, JobStatus, User
from app.schemas import IngestResponse, IngestURLRequest

router = APIRouter(prefix="/ingest", tags=["ingestion"])

_MAX_BYTES = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024


def _assert_no_ssrf(url: str) -> None:
    """Raise HTTPException if the URL resolves to a private/internal address."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise HTTPException(status_code=422, detail="Only HTTPS URLs are accepted")
    host = parsed.hostname
    if not host:
        raise HTTPException(status_code=422, detail="URL has no host")
    try:
        results = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise HTTPException(status_code=422, detail="Cannot resolve host")
    for _, _, _, _, sockaddr in results:
        ip = ipaddress.ip_address(sockaddr[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
        ):
            raise HTTPException(status_code=422, detail="URL resolves to a disallowed address")


async def _enqueue_job(job_id: str) -> None:
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    pool = await create_pool(redis_settings)
    await pool.enqueue_job("process_document", job_id)
    await pool.aclose()


async def _get_or_create_exam(
    session: AsyncSession,
    filename: str,
    file_hash: str,
    uploaded_by_id: uuid.UUID | None = None,
) -> Exam:
    result = await session.execute(select(Exam).where(Exam.file_hash == file_hash))
    existing = result.scalar_one_or_none()
    if existing:
        return existing
    exam = Exam(id=uuid.uuid4(), filename=filename, file_hash=file_hash, uploaded_by_id=uploaded_by_id)
    session.add(exam)
    await session.flush()
    return exam


async def _get_or_create_edital(
    session: AsyncSession,
    filename: str,
    file_hash: str,
    uploaded_by_id: uuid.UUID | None = None,
) -> Edital:
    result = await session.execute(select(Edital).where(Edital.file_hash == file_hash))
    existing = result.scalar_one_or_none()
    if existing:
        return existing
    edital = Edital(id=uuid.uuid4(), filename=filename, file_hash=file_hash, uploaded_by_id=uploaded_by_id)
    session.add(edital)
    await session.flush()
    return edital


async def _create_job(
    session: AsyncSession,
    category: DocumentCategory,
    exam_id: uuid.UUID | None = None,
    edital_id: uuid.UUID | None = None,
) -> Job:
    job = Job(
        id=uuid.uuid4(),
        exam_id=exam_id,
        edital_id=edital_id,
        category=category,
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


def _safe_filename(file_hash: str, original: str) -> str:
    return f"{file_hash[:16]}_{Path(original).name}"


@router.post("/upload", response_model=IngestResponse, status_code=202)
async def ingest_upload(
    file: UploadFile = File(...),
    category: DocumentCategory = Form(...),
    session: AsyncSession = Depends(get_session),
    current_user: User | None = Depends(get_current_user),
):
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
    original_name = file.filename or "upload.pdf"
    safe_name = _safe_filename(file_hash, original_name)

    user_id = current_user.id if current_user else None

    if category == DocumentCategory.edital:
        edital = await _get_or_create_edital(session, original_name, file_hash, uploaded_by_id=user_id)
        job = await _create_job(session, category, edital_id=edital.id)
        exam_id_out, edital_id_out = None, edital.id
    else:
        exam = await _get_or_create_exam(session, original_name, file_hash, uploaded_by_id=user_id)
        job = await _create_job(session, category, exam_id=exam.id)
        exam_id_out, edital_id_out = exam.id, None
    await session.commit()

    await _save_upload(content, safe_name)
    await _enqueue_job(str(job.id))

    return IngestResponse(job_id=job.id, exam_id=exam_id_out, edital_id=edital_id_out)


@router.post("/url", response_model=IngestResponse, status_code=202)
async def ingest_url(
    body: IngestURLRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User | None = Depends(get_current_user),
):
    url = body.url
    _assert_no_ssrf(url)

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=5.0),
            follow_redirects=False,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            content = response.content
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Remote server returned HTTP {exc.response.status_code}",
        ) from exc
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="Failed to fetch the URL")

    if len(content) == 0:
        raise HTTPException(status_code=422, detail="Downloaded file is empty")

    if len(content) > _MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum allowed size of {settings.MAX_UPLOAD_SIZE_MB} MB",
        )

    if not content.startswith(b"%PDF"):
        raise HTTPException(status_code=422, detail="Downloaded content does not appear to be a valid PDF")

    url_path = url.split("?")[0]
    original_name = url_path.split("/")[-1] or "remote.pdf"
    if not original_name.lower().endswith(".pdf"):
        original_name += ".pdf"

    file_hash = hashlib.sha256(content).hexdigest()
    safe_name = _safe_filename(file_hash, original_name)

    user_id = current_user.id if current_user else None

    if body.category == DocumentCategory.edital:
        edital = await _get_or_create_edital(session, original_name, file_hash, uploaded_by_id=user_id)
        job = await _create_job(session, body.category, edital_id=edital.id)
        exam_id_out, edital_id_out = None, edital.id
    else:
        exam = await _get_or_create_exam(session, original_name, file_hash, uploaded_by_id=user_id)
        job = await _create_job(session, body.category, exam_id=exam.id)
        exam_id_out, edital_id_out = exam.id, None
    await session.commit()

    await _save_upload(content, safe_name)
    await _enqueue_job(str(job.id))

    return IngestResponse(job_id=job.id, exam_id=exam_id_out, edital_id=edital_id_out)
