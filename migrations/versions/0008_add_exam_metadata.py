"""Add metadata fields to exams

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-05 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("exams", sa.Column("nome", sa.String(256), nullable=True))
    op.add_column("exams", sa.Column("periodo", sa.String(16), nullable=True))
    op.add_column("exams", sa.Column("tipo", sa.Integer(), nullable=True))
    op.add_column("exams", sa.Column("cor", sa.String(64), nullable=True))
    op.add_column("exams", sa.Column("tipo_prova", sa.String(128), nullable=True))


def downgrade() -> None:
    op.drop_column("exams", "tipo_prova")
    op.drop_column("exams", "cor")
    op.drop_column("exams", "tipo")
    op.drop_column("exams", "periodo")
    op.drop_column("exams", "nome")
