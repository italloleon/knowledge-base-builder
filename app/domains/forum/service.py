import uuid
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domains.forum.models import ForumReply, ForumThread, ForumUpvote
from app.domains.forum.schemas import ReplyCreate, ThreadCreate


class ForumService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_threads(self, question_id: uuid.UUID) -> list[ForumThread]:
        stmt = (
            select(ForumThread)
            .where(ForumThread.question_id == question_id)
            .order_by(ForumThread.is_pinned.desc(), ForumThread.created_at.desc())
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_thread(self, thread_id: uuid.UUID) -> ForumThread | None:
        stmt = (
            select(ForumThread)
            .where(ForumThread.id == thread_id)
            .options(selectinload(ForumThread.replies))
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def create_thread(self, author_id: uuid.UUID, data: ThreadCreate) -> ForumThread:
        thread = ForumThread(author_id=author_id, **data.model_dump())
        self.db.add(thread)
        await self.db.commit()
        await self.db.refresh(thread)
        return thread

    async def get_reply(self, reply_id: uuid.UUID) -> ForumReply | None:
        result = await self.db.execute(select(ForumReply).where(ForumReply.id == reply_id))
        return result.scalar_one_or_none()

    async def create_reply(
        self, thread_id: uuid.UUID, author_id: uuid.UUID, data: ReplyCreate
    ) -> ForumReply:
        thread_result = await self.db.execute(
            select(ForumThread).where(ForumThread.id == thread_id)
        )
        thread = thread_result.scalar_one_or_none()
        if thread is None:
            raise ValueError("Thread not found")

        reply = ForumReply(thread_id=thread_id, author_id=author_id, **data.model_dump())
        thread.reply_count += 1
        self.db.add(reply)
        await self.db.commit()
        await self.db.refresh(reply)
        return reply

    async def accept_reply(self, reply: ForumReply, requester_id: uuid.UUID) -> ForumReply:
        """Mark reply as accepted answer. Only the thread author can do this."""
        thread_result = await self.db.execute(
            select(ForumThread).where(ForumThread.id == reply.thread_id)
        )
        thread = thread_result.scalar_one_or_none()
        if thread is None or thread.author_id != requester_id:
            raise PermissionError("Only the thread author can accept a reply")

        # unaccept any previous accepted reply in this thread
        prev_stmt = select(ForumReply).where(
            ForumReply.thread_id == reply.thread_id, ForumReply.is_accepted.is_(True)
        )
        for prev in (await self.db.execute(prev_stmt)).scalars().all():
            prev.is_accepted = False

        reply.is_accepted = True
        await self.db.commit()
        await self.db.refresh(reply)
        return reply

    async def toggle_upvote(self, reply_id: uuid.UUID, user_id: uuid.UUID) -> int:
        """Add or remove upvote. Returns new upvote_count."""
        existing = (
            await self.db.execute(
                select(ForumUpvote).where(
                    ForumUpvote.reply_id == reply_id, ForumUpvote.user_id == user_id
                )
            )
        ).scalar_one_or_none()

        reply_result = await self.db.execute(select(ForumReply).where(ForumReply.id == reply_id))
        reply = reply_result.scalar_one_or_none()
        if reply is None:
            raise ValueError("Reply not found")

        if existing:
            await self.db.delete(existing)
            reply.upvote_count = max(0, reply.upvote_count - 1)
        else:
            self.db.add(ForumUpvote(reply_id=reply_id, user_id=user_id))
            reply.upvote_count += 1

        await self.db.commit()
        return reply.upvote_count
