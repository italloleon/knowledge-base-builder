"""Link study notes to timer sessions (optional FK)

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-11 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        ALTER TABLE app.study_notes
        ADD COLUMN IF NOT EXISTS study_session_id UUID
            REFERENCES app.study_sessions(id) ON DELETE SET NULL
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_app_study_notes_session "
        "ON app.study_notes (study_session_id) "
        "WHERE study_session_id IS NOT NULL"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_app_study_notes_session"))
    conn.execute(sa.text("ALTER TABLE app.study_notes DROP COLUMN IF EXISTS study_session_id"))
