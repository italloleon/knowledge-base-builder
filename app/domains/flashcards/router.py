import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_account
from app.database import get_session
from app.domains.flashcards.schemas import FlashcardCreate, FlashcardRead, FlashcardReview, FlashcardUpdate
from app.domains.flashcards.service import FlashcardService
from app.models import User

router = APIRouter(prefix="/flashcards", tags=["flashcards"])


def _svc(db: AsyncSession = Depends(get_session)) -> FlashcardService:
    return FlashcardService(db)


@router.get("/", response_model=list[FlashcardRead])
async def list_flashcards(
    due_only: Annotated[bool, Query(description="Return only cards due for review")] = False,
    user: User = Depends(require_account),
    svc: FlashcardService = Depends(_svc),
):
    return await svc.list_for_user(user.id, due_only=due_only)


@router.post("/", response_model=FlashcardRead, status_code=201)
async def create_flashcard(
    body: FlashcardCreate,
    user: User = Depends(require_account),
    svc: FlashcardService = Depends(_svc),
):
    return await svc.create(user.id, body)


@router.patch("/{card_id}", response_model=FlashcardRead)
async def update_flashcard(
    card_id: uuid.UUID,
    body: FlashcardUpdate,
    user: User = Depends(require_account),
    svc: FlashcardService = Depends(_svc),
):
    card = await svc.get(card_id, user.id)
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flashcard not found")
    return await svc.update(card, body)


@router.post("/{card_id}/review", response_model=FlashcardRead)
async def review_flashcard(
    card_id: uuid.UUID,
    body: FlashcardReview,
    user: User = Depends(require_account),
    svc: FlashcardService = Depends(_svc),
):
    """Submit a review result. quality 0–2 = failed, 3–5 = passed."""
    card = await svc.get(card_id, user.id)
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flashcard not found")
    return await svc.review(card, body.quality)


@router.delete("/{card_id}", status_code=204)
async def delete_flashcard(
    card_id: uuid.UUID,
    user: User = Depends(require_account),
    svc: FlashcardService = Depends(_svc),
):
    card = await svc.get(card_id, user.id)
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flashcard not found")
    await svc.delete(card)
