import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ThreadCreate(BaseModel):
    question_id: uuid.UUID
    title: str = Field(..., min_length=3, max_length=512)
    body: str = Field(..., min_length=10, max_length=10000)


class ThreadRead(BaseModel):
    id: uuid.UUID
    question_id: uuid.UUID
    author_id: uuid.UUID
    title: str
    body: str
    is_pinned: bool
    is_locked: bool
    reply_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ReplyCreate(BaseModel):
    body: str = Field(..., min_length=5, max_length=10000)


class ReplyRead(BaseModel):
    id: uuid.UUID
    thread_id: uuid.UUID
    author_id: uuid.UUID
    body: str
    is_accepted: bool
    upvote_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ThreadWithRepliesRead(ThreadRead):
    replies: list[ReplyRead] = []
