from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import EntityMixin
from app.database import Base
from app.models import CURADORIA_SCHEMA
from app.models_app import APP_SCHEMA


class ForumThread(EntityMixin, Base):
    __tablename__ = "forum_threads"
    __table_args__ = {"schema": APP_SCHEMA}

    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{CURADORIA_SCHEMA}.questions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{CURADORIA_SCHEMA}.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reply_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    replies: Mapped[list["ForumReply"]] = relationship(
        "ForumReply", back_populates="thread", passive_deletes=True
    )


class ForumReply(EntityMixin, Base):
    __tablename__ = "forum_replies"
    __table_args__ = {"schema": APP_SCHEMA}

    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_SCHEMA}.forum_threads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{CURADORIA_SCHEMA}.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    upvote_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    thread: Mapped["ForumThread"] = relationship("ForumThread", back_populates="replies")
    upvotes: Mapped[list["ForumUpvote"]] = relationship(
        "ForumUpvote", back_populates="reply", passive_deletes=True
    )


class ForumUpvote(EntityMixin, Base):
    """One upvote per user per reply — enforced by unique constraint."""

    __tablename__ = "forum_upvotes"
    __table_args__ = (
        UniqueConstraint("reply_id", "user_id", name="uq_forum_upvote_user_reply"),
        {"schema": APP_SCHEMA},
    )

    reply_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_SCHEMA}.forum_replies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{CURADORIA_SCHEMA}.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    reply: Mapped["ForumReply"] = relationship("ForumReply", back_populates="upvotes")
