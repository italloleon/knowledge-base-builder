"""Public app schema — accounts, subscriptions, simulados, attempts

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-10 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS app"))

    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE app_subscription_status_enum AS ENUM
                ('trialing', 'active', 'past_due', 'canceled', 'incomplete');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$
    """))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS app.users (
            id              UUID PRIMARY KEY,
            email           VARCHAR(256) NOT NULL,
            password_hash   VARCHAR(256),
            full_name       VARCHAR(256) NOT NULL,
            is_active       BOOLEAN NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_app_users_email UNIQUE (email)
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_app_users_email ON app.users (email)"
    ))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS app.subscriptions (
            id                      UUID PRIMARY KEY,
            user_id                 UUID NOT NULL
                REFERENCES app.users(id) ON DELETE CASCADE,
            status                  app_subscription_status_enum NOT NULL DEFAULT 'incomplete',
            plan_key                VARCHAR(64) NOT NULL,
            current_period_end      TIMESTAMPTZ,
            external_customer_id    VARCHAR(128),
            created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_app_subscriptions_user_id ON app.subscriptions (user_id)"
    ))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS app.simulados (
            id              UUID PRIMARY KEY,
            user_id         UUID NOT NULL
                REFERENCES app.users(id) ON DELETE CASCADE,
            title           VARCHAR(512) NOT NULL,
            description     TEXT,
            exam_id         UUID
                REFERENCES curadoria.exams(id) ON DELETE SET NULL,
            config          JSONB,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_app_simulados_user_id ON app.simulados (user_id)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_app_simulados_exam_id ON app.simulados (exam_id)"
    ))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS app.simulado_attempts (
            id              UUID PRIMARY KEY,
            simulado_id     UUID NOT NULL
                REFERENCES app.simulados(id) ON DELETE CASCADE,
            user_id         UUID NOT NULL
                REFERENCES app.users(id) ON DELETE CASCADE,
            started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at     TIMESTAMPTZ,
            score_percent   DOUBLE PRECISION,
            answers         JSONB,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_app_attempts_simulado_id ON app.simulado_attempts (simulado_id)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_app_attempts_user_id ON app.simulado_attempts (user_id)"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP SCHEMA IF EXISTS app CASCADE"))
    conn.execute(sa.text("DROP TYPE IF EXISTS app_subscription_status_enum"))
