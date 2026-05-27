"""ORM models for the public consumer app — PostgreSQL schema `app`.

These tables are independent from `curadoria` admin accounts unless you later
link them (e.g. shared SSO). Exam content references `curadoria.exams` when a
simulado is tied to a parsed exam.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models import CURADORIA_SCHEMA

if TYPE_CHECKING:
    from app.models import Exam

APP_SCHEMA = "app"


class SubscriptionStatus(str, enum.Enum):
    trialing = "trialing"
    active = "active"
    past_due = "past_due"
    canceled = "canceled"
    incomplete = "incomplete"


class AppUser(Base):
    """End-user account for the public app (schema `app.users`)."""

    __tablename__ = "users"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(256), nullable=False, unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    full_name: Mapped[str] = mapped_column(String(256), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    subscriptions: Mapped[list["Subscription"]] = relationship(
        "Subscription", back_populates="user", passive_deletes=True
    )
    simulados: Mapped[list["Simulado"]] = relationship(
        "Simulado", back_populates="owner", passive_deletes=True
    )
    attempts: Mapped[list["SimuladoAttempt"]] = relationship(
        "SimuladoAttempt", back_populates="user", passive_deletes=True
    )


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_SCHEMA}.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="app_subscription_status_enum"),
        nullable=False,
        default=SubscriptionStatus.incomplete,
    )
    plan_key: Mapped[str] = mapped_column(String(64), nullable=False)
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    external_customer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped["AppUser"] = relationship("AppUser", back_populates="subscriptions")


class Simulado(Base):
    """A study/practice session owned by a public-app user."""

    __tablename__ = "simulados"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_SCHEMA}.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    exam_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{CURADORIA_SCHEMA}.exams.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    owner: Mapped["AppUser"] = relationship("AppUser", back_populates="simulados")
    exam: Mapped["Exam | None"] = relationship("Exam", foreign_keys=[exam_id])
    attempts: Mapped[list["SimuladoAttempt"]] = relationship(
        "SimuladoAttempt", back_populates="simulado", passive_deletes=True
    )


class SimuladoAttempt(Base):
    """One completed or in-progress run of a simulado (tentativa)."""

    __tablename__ = "simulado_attempts"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    simulado_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_SCHEMA}.simulados.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_SCHEMA}.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    score_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    answers: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    simulado: Mapped["Simulado"] = relationship("Simulado", back_populates="attempts")
    user: Mapped["AppUser"] = relationship("AppUser", back_populates="attempts")
