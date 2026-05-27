"""Study workspace — Pomodoro-style sessions + tagged notes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_account
from app.database import get_session
from app.models import Edital, User
from app.models_study import StudyNote, StudySession
from app.schemas import (
    StudyMetricsResponse,
    StudyNoteCreate,
    StudyNoteResponse,
    StudyNoteUpdate,
    StudySessionResponse,
    StudySessionStartRequest,
    StudySessionWithNotesResponse,
    TagOptionsResponse,
)
from app.study_tags import flatten_tags_from_editais

router = APIRouter(prefix="/study", tags=["study"])


def _normalize_tags(raw: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in raw:
        s = str(t).strip()
        if len(s) < 1 or len(s) > 240:
            continue
        k = s.casefold()
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
        if len(out) >= 40:
            break
    return out


@router.get("/tag-options", response_model=TagOptionsResponse)
async def list_tag_options(session: AsyncSession = Depends(get_session), _: User = Depends(require_account)):
    rows = (
        (
            await session.execute(
                select(Edital.edition_name, Edital.numero_edital, Edital.knowledge_areas)
            )
        )
        .all()
    )
    tags = flatten_tags_from_editais(rows)
    return TagOptionsResponse(tags=tags)


@router.post("/sessions/start", response_model=StudySessionResponse, status_code=201)
async def start_session(
    body: StudySessionStartRequest,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(require_account),
):
    now = datetime.now(UTC)
    row = StudySession(
        id=uuid.uuid4(),
        user_id=user.id,
        planned_duration_seconds=body.planned_duration_seconds,
        started_at=now,
        ended_at=None,
        duration_seconds=None,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return StudySessionResponse.model_validate(row)


@router.post("/sessions/{session_id}/stop", response_model=StudySessionResponse)
async def stop_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(require_account),
):
    result = await db.execute(select(StudySession).where(StudySession.id == session_id))
    row = result.scalar_one_or_none()
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if row.ended_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session already stopped")

    ended = datetime.now(UTC)
    delta = int((ended - row.started_at).total_seconds())
    row.ended_at = ended
    row.duration_seconds = max(0, delta)
    await db.commit()
    await db.refresh(row)
    return StudySessionResponse.model_validate(row)


@router.get("/sessions", response_model=list[StudySessionWithNotesResponse])
async def list_sessions(
    db: AsyncSession = Depends(get_session),
    user: User = Depends(require_account),
    limit: Annotated[int, Query(ge=1, le=200)] = 80,
):
    stmt = (
        select(StudySession)
        .where(StudySession.user_id == user.id)
        .order_by(StudySession.started_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    if not rows:
        return []

    session_ids = [r.id for r in rows]
    notes_stmt = (
        select(StudyNote)
        .where(StudyNote.study_session_id.in_(session_ids))
        .order_by(StudyNote.created_at.asc())
    )
    note_rows = (await db.execute(notes_stmt)).scalars().all()
    by_session: dict[uuid.UUID, list[StudyNote]] = {}
    for n in note_rows:
        sid = n.study_session_id
        if sid is None:
            continue
        by_session.setdefault(sid, []).append(n)

    out: list[StudySessionWithNotesResponse] = []
    for r in rows:
        base = StudySessionResponse.model_validate(r).model_dump()
        notes_list = [StudyNoteResponse.model_validate(x) for x in by_session.get(r.id, [])]
        out.append(StudySessionWithNotesResponse(**base, notes=notes_list))
    return out


@router.get("/metrics", response_model=StudyMetricsResponse)
async def study_metrics(db: AsyncSession = Depends(get_session), user: User = Depends(require_account)):
    week_ago = datetime.now(UTC) - timedelta(days=7)

    week_stmt = select(func.coalesce(func.sum(StudySession.duration_seconds), 0)).where(
        StudySession.user_id == user.id,
        StudySession.ended_at.isnot(None),
        StudySession.ended_at >= week_ago,
    )
    week_total = int((await db.execute(week_stmt)).scalar_one())

    week_count_stmt = select(func.count()).select_from(StudySession).where(
        StudySession.user_id == user.id,
        StudySession.ended_at.isnot(None),
        StudySession.ended_at >= week_ago,
    )
    week_count = int((await db.execute(week_count_stmt)).scalar_one())

    all_sum_stmt = select(func.coalesce(func.sum(StudySession.duration_seconds), 0)).where(
        StudySession.user_id == user.id,
        StudySession.ended_at.isnot(None),
    )
    all_total = int((await db.execute(all_sum_stmt)).scalar_one())

    all_count_stmt = select(func.count()).select_from(StudySession).where(
        StudySession.user_id == user.id,
        StudySession.ended_at.isnot(None),
    )
    all_count = int((await db.execute(all_count_stmt)).scalar_one())

    return StudyMetricsResponse(
        total_seconds_week=week_total,
        session_count_week=week_count,
        total_seconds_all=all_total,
        session_count_all=all_count,
    )


@router.get("/notes", response_model=list[StudyNoteResponse])
async def list_notes(
    db: AsyncSession = Depends(get_session),
    user: User = Depends(require_account),
    tag: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    stmt = select(StudyNote).where(StudyNote.user_id == user.id)
    if tag and tag.strip():
        stmt = stmt.where(StudyNote.tags.contains([tag.strip()]))
    stmt = stmt.order_by(StudyNote.updated_at.desc()).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return [StudyNoteResponse.model_validate(r) for r in rows]


@router.post("/notes", response_model=StudyNoteResponse, status_code=201)
async def create_note(
    body: StudyNoteCreate,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(require_account),
):
    tags = _normalize_tags(body.tags)
    session_fk: uuid.UUID | None = None
    if body.study_session_id is not None:
        result = await db.execute(
            select(StudySession).where(
                StudySession.id == body.study_session_id,
                StudySession.user_id == user.id,
            )
        )
        sess = result.scalar_one_or_none()
        if sess is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        if sess.ended_at is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Sessão já encerrada; não é possível associar a nota.",
            )
        session_fk = sess.id

    row = StudyNote(
        id=uuid.uuid4(),
        user_id=user.id,
        study_session_id=session_fk,
        title=body.title,
        body=body.body,
        tags=tags,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return StudyNoteResponse.model_validate(row)


@router.patch("/notes/{note_id}", response_model=StudyNoteResponse)
async def update_note(
    note_id: uuid.UUID,
    body: StudyNoteUpdate,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(require_account),
):
    result = await db.execute(select(StudyNote).where(StudyNote.id == note_id))
    row = result.scalar_one_or_none()
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")

    if body.title is not None:
        row.title = body.title
    if body.body is not None:
        row.body = body.body
    if body.tags is not None:
        row.tags = _normalize_tags(body.tags)

    row.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(row)
    return StudyNoteResponse.model_validate(row)


@router.delete("/notes/{note_id}", status_code=204)
async def delete_note(
    note_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(require_account),
):
    result = await db.execute(select(StudyNote).where(StudyNote.id == note_id))
    row = result.scalar_one_or_none()
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    await db.delete(row)
    await db.commit()
