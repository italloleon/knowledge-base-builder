"""Add flashcards and forum tables (app schema)

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-27 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── Flashcards ─────────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS app.flashcards (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id         UUID NOT NULL
                REFERENCES curadoria.users(id) ON DELETE CASCADE,
            question_id     UUID
                REFERENCES curadoria.questions(id) ON DELETE SET NULL,
            front           TEXT NOT NULL,
            back            TEXT NOT NULL,
            tags            TEXT[] NOT NULL DEFAULT '{}',
            next_review_at  TIMESTAMPTZ,
            interval_days   INTEGER NOT NULL DEFAULT 1,
            ease_factor     DOUBLE PRECISION NOT NULL DEFAULT 2.5,
            is_suspended    BOOLEAN NOT NULL DEFAULT FALSE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_app_flashcards_user "
        "ON app.flashcards (user_id)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_app_flashcards_review "
        "ON app.flashcards (user_id, next_review_at ASC NULLS FIRST) "
        "WHERE is_suspended = FALSE"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_app_flashcards_question "
        "ON app.flashcards (question_id) WHERE question_id IS NOT NULL"
    ))

    # ── Forum threads ──────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS app.forum_threads (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            question_id UUID NOT NULL
                REFERENCES curadoria.questions(id) ON DELETE CASCADE,
            author_id   UUID NOT NULL
                REFERENCES curadoria.users(id) ON DELETE CASCADE,
            title       VARCHAR(512) NOT NULL,
            body        TEXT NOT NULL,
            is_pinned   BOOLEAN NOT NULL DEFAULT FALSE,
            is_locked   BOOLEAN NOT NULL DEFAULT FALSE,
            reply_count INTEGER NOT NULL DEFAULT 0,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_app_forum_threads_question "
        "ON app.forum_threads (question_id, is_pinned DESC, created_at DESC)"
    ))

    # ── Forum replies ──────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS app.forum_replies (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            thread_id    UUID NOT NULL
                REFERENCES app.forum_threads(id) ON DELETE CASCADE,
            author_id    UUID NOT NULL
                REFERENCES curadoria.users(id) ON DELETE CASCADE,
            body         TEXT NOT NULL,
            is_accepted  BOOLEAN NOT NULL DEFAULT FALSE,
            upvote_count INTEGER NOT NULL DEFAULT 0,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_app_forum_replies_thread "
        "ON app.forum_replies (thread_id, created_at ASC)"
    ))

    # ── Forum upvotes ──────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS app.forum_upvotes (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            reply_id    UUID NOT NULL
                REFERENCES app.forum_replies(id) ON DELETE CASCADE,
            user_id     UUID NOT NULL
                REFERENCES curadoria.users(id) ON DELETE CASCADE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_forum_upvote_user_reply UNIQUE (reply_id, user_id)
        )
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP TABLE IF EXISTS app.forum_upvotes CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS app.forum_replies CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS app.forum_threads CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS app.flashcards CASCADE"))
