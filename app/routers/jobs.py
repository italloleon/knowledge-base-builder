"""Job status and management endpoints."""

import logging
import uuid

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.config import settings
from app.database import get_session
from app.models import Job, JobStatus
from app.schemas import JobResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])

_ACTIVE_STATUSES = (JobStatus.pending, JobStatus.processing)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ---------------------------------------------------------------------------
# Admin: list + cancel
# ---------------------------------------------------------------------------


@router.get("", response_model=list[JobResponse])
async def list_jobs(
    active_only: bool = False,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
):
    """List recent jobs, newest first.

    Pass ``active_only=true`` to see only pending/processing jobs.
    """
    stmt = select(Job)
    if active_only:
        stmt = stmt.where(or_(Job.status == s for s in _ACTIVE_STATUSES))
    stmt = stmt.order_by(desc(Job.created_at)).limit(min(limit, 200))
    result = await session.execute(stmt)
    return result.scalars().all()


@router.post("/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Cancel a pending or processing job.

    - Pending jobs: removed from the ARQ queue before they start.
    - Processing jobs: ARQ abort signal is sent; the current operation
      (e.g. Docling extraction) runs to completion but no further work
      is dispatched. The DB status is set to ``failed`` immediately so
      the UI reflects the cancellation right away.
    """
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status not in _ACTIVE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Job is already in terminal state: {job.status.value}",
        )

    # Send abort signal to ARQ (best-effort — won't interrupt blocking ops)
    try:
        pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
        await pool.abort_job(str(job_id))
        await pool.aclose()
    except Exception as exc:  # noqa: BLE001
        logger.warning("cancel_job: ARQ abort failed for %s — %s", job_id, exc)

    # Mark as failed in the DB immediately for instant UI feedback
    job.status = JobStatus.failed
    job.error_message = "Cancelled by user"
    session.add(job)
    await session.commit()
    await session.refresh(job)

    logger.info("cancel_job: job %s cancelled", job_id)
    return job
