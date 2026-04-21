"""Health probes — liveness and readiness."""

import redis.asyncio as aioredis
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.database import AsyncSessionLocal
from app.schemas import HealthLive, HealthReady

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=HealthLive)
async def liveness():
    return HealthLive(status="ok")


@router.get("/ready")
async def readiness():
    db_status = "ok"
    redis_status = "ok"
    overall = "ok"

    # Check database
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        db_status = f"error: {exc}"
        overall = "degraded"

    # Check Redis
    try:
        client = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        await client.ping()
        await client.aclose()
    except Exception as exc:  # noqa: BLE001
        redis_status = f"error: {exc}"
        overall = "degraded"

    body = HealthReady(status=overall, database=db_status, redis=redis_status)
    status_code = 200 if overall == "ok" else 503
    return JSONResponse(content=body.model_dump(), status_code=status_code)
