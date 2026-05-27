import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class FlashcardCreate(BaseModel):
    front: str = Field(..., min_length=1, max_length=2000)
    back: str = Field(..., min_length=1, max_length=2000)
    question_id: uuid.UUID | None = None
    tags: list[str] = []


class FlashcardUpdate(BaseModel):
    front: str | None = Field(None, min_length=1, max_length=2000)
    back: str | None = Field(None, min_length=1, max_length=2000)
    tags: list[str] | None = None
    is_suspended: bool | None = None


class FlashcardReview(BaseModel):
    """SM-2 review quality: 0 = complete blackout, 5 = perfect recall."""
    quality: int = Field(..., ge=0, le=5)


class FlashcardRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    question_id: uuid.UUID | None
    front: str
    back: str
    tags: list[str]
    next_review_at: datetime | None
    interval_days: int
    ease_factor: float
    is_suspended: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
