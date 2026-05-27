"""Move application tables into schema `curadoria`

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-10 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS curadoria"))

    # Idempotent: only moves tables still in public (safe to re-run).
    conn.execute(
        sa.text(
            """
            DO $$
            DECLARE
              r RECORD;
            BEGIN
              FOR r IN
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public'
                  AND tablename IN (
                    'users', 'refresh_tokens', 'editais', 'exams', 'jobs',
                    'questions', 'parse_errors', 'question_opinions'
                  )
              LOOP
                EXECUTE format('ALTER TABLE public.%I SET SCHEMA curadoria', r.tablename);
              END LOOP;
            END $$;
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            DO $$
            DECLARE
              r RECORD;
            BEGIN
              FOR r IN
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'curadoria'
                  AND tablename IN (
                    'users', 'refresh_tokens', 'editais', 'exams', 'jobs',
                    'questions', 'parse_errors', 'question_opinions'
                  )
              LOOP
                EXECUTE format('ALTER TABLE curadoria.%I SET SCHEMA public', r.tablename);
              END LOOP;
            END $$;
            """
        )
    )
