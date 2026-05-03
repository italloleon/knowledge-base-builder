"""Add editais table, edital_id FK on exams and jobs

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-24 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS editais (
            id                          UUID         PRIMARY KEY,
            filename                    VARCHAR(512) NOT NULL,
            file_hash                   VARCHAR(64)  NOT NULL,
            numero_edital               VARCHAR(32),
            ano                         INTEGER,
            edition_name                VARCHAR(64),
            organizadora                VARCHAR(128),
            instituicao_gestora         VARCHAR(128),
            modalidade                  TEXT,
            total_questoes_gerais       INTEGER,
            total_questoes_especificas  INTEGER,
            percentual_minimo_aprovacao FLOAT,
            bolsa_mensal                FLOAT,
            data_inicio_programas       VARCHAR(128),
            contato_email               VARCHAR(256),
            contato_telefone            VARCHAR(32),
            url_enare                   VARCHAR(512),
            cronograma                  JSONB,
            vagas                       JSONB,
            instituicoes                JSONB,
            knowledge_areas             JSONB,
            created_at                  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_editais_file_hash UNIQUE (file_hash)
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_editais_file_hash ON editais (file_hash)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_editais_ano ON editais (ano)"
    ))

    # Add edital_id FK to exams (nullable — prova linked to its edital)
    conn.execute(sa.text("""
        ALTER TABLE exams
            ADD COLUMN IF NOT EXISTS edital_id UUID
                REFERENCES editais(id) ON DELETE SET NULL
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_exams_edital_id ON exams (edital_id)"
    ))

    # Add edital_id FK to jobs (nullable — tracks which job processed which edital)
    conn.execute(sa.text("""
        ALTER TABLE jobs
            ADD COLUMN IF NOT EXISTS edital_id UUID
                REFERENCES editais(id) ON DELETE SET NULL
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_jobs_edital_id ON jobs (edital_id)"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_jobs_edital_id"))
    conn.execute(sa.text("ALTER TABLE jobs  DROP COLUMN IF EXISTS edital_id"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_exams_edital_id"))
    conn.execute(sa.text("ALTER TABLE exams DROP COLUMN IF EXISTS edital_id"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_editais_ano"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_editais_file_hash"))
    conn.execute(sa.text("DROP TABLE IF EXISTS editais CASCADE"))
