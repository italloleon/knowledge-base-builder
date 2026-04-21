"""Initial schema — exams, jobs, questions, parse_errors

Revision ID: 0001
Revises:
Create Date: 2025-01-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE job_status_enum AS ENUM
                ('pending', 'processing', 'completed', 'failed', 'partial');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$
    """))
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE section_type_enum AS ENUM
                ('conhecimentos_gerais', 'conhecimentos_especificos', 'unknown');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$
    """))
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE question_type_enum AS ENUM
                ('simple', 'roman_numeral', 'true_false', 'association', 'unknown');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$
    """))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS exams (
            id          UUID PRIMARY KEY,
            filename    VARCHAR(512) NOT NULL,
            file_hash   VARCHAR(64)  NOT NULL,
            created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_exams_file_hash UNIQUE (file_hash)
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_exams_file_hash ON exams (file_hash)"
    ))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS jobs (
            id            UUID            PRIMARY KEY,
            exam_id       UUID            REFERENCES exams(id) ON DELETE SET NULL,
            status        job_status_enum NOT NULL DEFAULT 'pending',
            total_found   INTEGER         NOT NULL DEFAULT 0,
            parsed_ok     INTEGER         NOT NULL DEFAULT 0,
            parse_errors  INTEGER         NOT NULL DEFAULT 0,
            error_message TEXT,
            created_at    TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
            updated_at    TIMESTAMPTZ     NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_jobs_exam_id ON jobs (exam_id)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_jobs_status  ON jobs (status)"
    ))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS questions (
            id            UUID                PRIMARY KEY,
            exam_id       UUID                NOT NULL REFERENCES exams(id)  ON DELETE CASCADE,
            job_id        UUID                NOT NULL REFERENCES jobs(id)   ON DELETE CASCADE,
            number        INTEGER             NOT NULL,
            section       section_type_enum   NOT NULL DEFAULT 'unknown',
            question_type question_type_enum  NOT NULL DEFAULT 'unknown',
            enunciado     TEXT                NOT NULL,
            items         JSONB,
            alternatives  JSONB               NOT NULL DEFAULT '{}',
            gabarito      VARCHAR(1),
            raw_block     TEXT                NOT NULL,
            confidence    FLOAT               NOT NULL DEFAULT 1.0,
            created_at    TIMESTAMPTZ         NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_questions_exam_id    ON questions (exam_id)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_questions_job_id     ON questions (job_id)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_questions_number     ON questions (exam_id, number)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_questions_section    ON questions (exam_id, section)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_questions_confidence ON questions (confidence)"
    ))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS parse_errors (
            id         UUID        PRIMARY KEY,
            exam_id    UUID        NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
            job_id     UUID        NOT NULL REFERENCES jobs(id)  ON DELETE CASCADE,
            raw_block  TEXT        NOT NULL,
            reason     TEXT        NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_parse_errors_exam_id ON parse_errors (exam_id)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_parse_errors_job_id  ON parse_errors (job_id)"
    ))

    conn.execute(sa.text("""
        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """))
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TRIGGER jobs_updated_at
                BEFORE UPDATE ON jobs
                FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
        EXCEPTION WHEN duplicate_object THEN null;
        END $$
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP TRIGGER  IF EXISTS jobs_updated_at         ON jobs"))
    conn.execute(sa.text("DROP FUNCTION IF EXISTS update_updated_at_column()"))
    conn.execute(sa.text("DROP TABLE    IF EXISTS parse_errors CASCADE"))
    conn.execute(sa.text("DROP TABLE    IF EXISTS questions    CASCADE"))
    conn.execute(sa.text("DROP TABLE    IF EXISTS jobs         CASCADE"))
    conn.execute(sa.text("DROP TABLE    IF EXISTS exams        CASCADE"))
    conn.execute(sa.text("DROP TYPE     IF EXISTS question_type_enum"))
    conn.execute(sa.text("DROP TYPE     IF EXISTS section_type_enum"))
    conn.execute(sa.text("DROP TYPE     IF EXISTS job_status_enum"))
