"""Add category column to jobs

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-21 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE document_category_enum AS ENUM ('prova', 'edital');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$
    """))
    conn.execute(sa.text("""
        ALTER TABLE jobs
            ADD COLUMN IF NOT EXISTS category document_category_enum NOT NULL DEFAULT 'prova'
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE jobs DROP COLUMN IF EXISTS category"))
    conn.execute(sa.text("DROP TYPE IF EXISTS document_category_enum"))
