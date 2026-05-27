"""Add images column to questions

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-21 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "questions",
        sa.Column("images", JSONB, nullable=True),
        schema="curadoria",
    )


def downgrade() -> None:
    op.drop_column("questions", "images", schema="curadoria")
