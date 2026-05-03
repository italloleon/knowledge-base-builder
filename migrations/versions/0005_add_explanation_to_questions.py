"""Add explanation columns to questions

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        ALTER TABLE questions
            ADD COLUMN IF NOT EXISTS explanation JSONB,
            ADD COLUMN IF NOT EXISTS explanation_flagged BOOLEAN NOT NULL DEFAULT FALSE
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        ALTER TABLE questions
            DROP COLUMN IF EXISTS explanation,
            DROP COLUMN IF EXISTS explanation_flagged
    """))
