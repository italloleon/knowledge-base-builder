import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.flashcards.models import Flashcard
from app.domains.flashcards.schemas import FlashcardCreate, FlashcardUpdate


def _sm2(ease: float, interval: int, quality: int) -> tuple[float, int]:
    """SM-2 algorithm: returns (new_ease_factor, new_interval_days)."""
    ease = max(1.3, ease + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    if quality < 3:
        interval = 1
    elif interval == 1:
        interval = 6
    else:
        interval = round(interval * ease)
    return ease, interval


class FlashcardService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_for_user(
        self, user_id: uuid.UUID, *, due_only: bool = False
    ) -> list[Flashcard]:
        stmt = select(Flashcard).where(
            Flashcard.user_id == user_id,
            ~Flashcard.is_suspended,
        )
        if due_only:
            now = datetime.now(UTC)
            stmt = stmt.where(
                (Flashcard.next_review_at.is_(None)) | (Flashcard.next_review_at <= now)
            )
        stmt = stmt.order_by(Flashcard.next_review_at.asc().nulls_first())
        return list((await self.db.execute(stmt)).scalars().all())

    async def get(self, card_id: uuid.UUID, user_id: uuid.UUID) -> Flashcard | None:
        result = await self.db.execute(
            select(Flashcard).where(Flashcard.id == card_id, Flashcard.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def create(self, user_id: uuid.UUID, data: FlashcardCreate) -> Flashcard:
        card = Flashcard(user_id=user_id, **data.model_dump())
        self.db.add(card)
        await self.db.commit()
        await self.db.refresh(card)
        return card

    async def update(self, card: Flashcard, data: FlashcardUpdate) -> Flashcard:
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(card, field, value)
        await self.db.commit()
        await self.db.refresh(card)
        return card

    async def review(self, card: Flashcard, quality: int) -> Flashcard:
        ease, interval = _sm2(card.ease_factor, card.interval_days, quality)
        card.ease_factor = ease
        card.interval_days = interval
        card.next_review_at = datetime.now(UTC) + timedelta(days=interval)
        await self.db.commit()
        await self.db.refresh(card)
        return card

    async def delete(self, card: Flashcard) -> None:
        await self.db.delete(card)
        await self.db.commit()
