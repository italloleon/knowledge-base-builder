"""Study sessions + notes (app schema)

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-11 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS app.study_sessions (
            id                          UUID PRIMARY KEY,
            user_id                     UUID NOT NULL
                REFERENCES curadoria.users(id) ON DELETE CASCADE,
            planned_duration_seconds    INTEGER,
            started_at                  TIMESTAMPTZ NOT NULL,
            ended_at                    TIMESTAMPTZ,
            duration_seconds            INTEGER,
            CONSTRAINT ck_study_session_duration_nonneg CHECK (
                duration_seconds IS NULL OR duration_seconds >= 0
            ),
            CONSTRAINT ck_study_session_times CHECK (
                ended_at IS NULL OR ended_at >= started_at
            )
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_app_study_sessions_user_started "
        "ON app.study_sessions (user_id, started_at DESC)"
    ))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS app.study_notes (
            id              UUID PRIMARY KEY,
            user_id         UUID NOT NULL
                REFERENCES curadoria.users(id) ON DELETE CASCADE,
            title           VARCHAR(512),
            body            TEXT NOT NULL DEFAULT '',
            tags            TEXT[] NOT NULL DEFAULT '{}',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_app_study_notes_user_updated "
        "ON app.study_notes (user_id, updated_at DESC)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_app_study_notes_tags ON app.study_notes USING GIN (tags)"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP TABLE IF EXISTS app.study_notes CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS app.study_sessions CASCADE"))
