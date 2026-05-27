import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_account
from app.database import get_session
from app.domains.forum.schemas import ReplyCreate, ReplyRead, ThreadCreate, ThreadRead, ThreadWithRepliesRead
from app.domains.forum.service import ForumService
from app.models import User

router = APIRouter(prefix="/forum", tags=["forum"])


def _svc(db: AsyncSession = Depends(get_session)) -> ForumService:
    return ForumService(db)


@router.get("/threads", response_model=list[ThreadRead])
async def list_threads(
    question_id: uuid.UUID,
    user: User = Depends(require_account),
    svc: ForumService = Depends(_svc),
):
    return await svc.list_threads(question_id)


@router.post("/threads", response_model=ThreadRead, status_code=201)
async def create_thread(
    body: ThreadCreate,
    user: User = Depends(require_account),
    svc: ForumService = Depends(_svc),
):
    return await svc.create_thread(user.id, body)


@router.get("/threads/{thread_id}", response_model=ThreadWithRepliesRead)
async def get_thread(
    thread_id: uuid.UUID,
    user: User = Depends(require_account),
    svc: ForumService = Depends(_svc),
):
    thread = await svc.get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")
    return thread


@router.post("/threads/{thread_id}/replies", response_model=ReplyRead, status_code=201)
async def create_reply(
    thread_id: uuid.UUID,
    body: ReplyCreate,
    user: User = Depends(require_account),
    svc: ForumService = Depends(_svc),
):
    try:
        return await svc.create_reply(thread_id, user.id, body)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post("/replies/{reply_id}/accept", response_model=ReplyRead)
async def accept_reply(
    reply_id: uuid.UUID,
    user: User = Depends(require_account),
    svc: ForumService = Depends(_svc),
):
    reply = await svc.get_reply(reply_id)
    if reply is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reply not found")
    try:
        return await svc.accept_reply(reply, user.id)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.post("/replies/{reply_id}/upvote", response_model=dict)
async def toggle_upvote(
    reply_id: uuid.UUID,
    user: User = Depends(require_account),
    svc: ForumService = Depends(_svc),
):
    try:
        count = await svc.toggle_upvote(reply_id, user.id)
        return {"upvote_count": count}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
