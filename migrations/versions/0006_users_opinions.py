"""Users, refresh tokens, uploaded_by FKs, and question opinions

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-03 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS users (
            id            UUID         PRIMARY KEY,
            email         VARCHAR(256) NOT NULL,
            full_name     VARCHAR(256) NOT NULL,
            password_hash VARCHAR(256) NOT NULL,
            is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_users_email UNIQUE (email)
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_users_email ON users (email)"
    ))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id          UUID        PRIMARY KEY,
            user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash  VARCHAR(64) NOT NULL,
            expires_at  TIMESTAMPTZ NOT NULL,
            revoked     BOOLEAN     NOT NULL DEFAULT FALSE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_refresh_tokens_hash UNIQUE (token_hash)
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_refresh_tokens_user_id ON refresh_tokens (user_id)"
    ))

    conn.execute(sa.text("""
        ALTER TABLE exams
            ADD COLUMN IF NOT EXISTS uploaded_by_id UUID REFERENCES users(id) ON DELETE SET NULL
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_exams_uploaded_by_id ON exams (uploaded_by_id)"
    ))

    conn.execute(sa.text("""
        ALTER TABLE editais
            ADD COLUMN IF NOT EXISTS uploaded_by_id UUID REFERENCES users(id) ON DELETE SET NULL
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_editais_uploaded_by_id ON editais (uploaded_by_id)"
    ))

    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE opinion_target_enum AS ENUM
                ('question', 'alternative_a', 'alternative_b', 'alternative_c',
                 'alternative_d', 'alternative_e');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$
    """))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS question_opinions (
            id          UUID                 PRIMARY KEY,
            question_id UUID                 NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
            user_id     UUID                 NOT NULL REFERENCES users(id)     ON DELETE CASCADE,
            target      opinion_target_enum  NOT NULL DEFAULT 'question',
            body        TEXT                 NOT NULL,
            created_at  TIMESTAMPTZ          NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ          NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_opinion_body_length CHECK (char_length(body) <= 5000)
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_opinions_question_id ON question_opinions (question_id)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_opinions_user_id ON question_opinions (user_id)"
    ))

    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TRIGGER question_opinions_updated_at
                BEFORE UPDATE ON question_opinions
                FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
        EXCEPTION WHEN duplicate_object THEN null;
        END $$
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP TRIGGER  IF EXISTS question_opinions_updated_at ON question_opinions"))
    conn.execute(sa.text("DROP TABLE    IF EXISTS question_opinions CASCADE"))
    conn.execute(sa.text("DROP TYPE     IF EXISTS opinion_target_enum"))
    conn.execute(sa.text("ALTER TABLE editais DROP COLUMN IF EXISTS uploaded_by_id"))
    conn.execute(sa.text("ALTER TABLE exams   DROP COLUMN IF EXISTS uploaded_by_id"))
    conn.execute(sa.text("DROP TABLE    IF EXISTS refresh_tokens CASCADE"))
    conn.execute(sa.text("DROP TABLE    IF EXISTS users CASCADE"))
