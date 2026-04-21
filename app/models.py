import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class JobStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    partial = "partial"


class DocumentCategory(str, enum.Enum):
    prova = "prova"
    edital = "edital"


class SectionType(str, enum.Enum):
    conhecimentos_gerais = "conhecimentos_gerais"
    conhecimentos_especificos = "conhecimentos_especificos"
    unknown = "unknown"


class QuestionType(str, enum.Enum):
    simple = "simple"
    roman_numeral = "roman_numeral"
    true_false = "true_false"
    association = "association"
    unknown = "unknown"


class Exam(Base):
    __tablename__ = "exams"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("file_hash", name="uq_exams_file_hash"),)

    jobs: Mapped[list["Job"]] = relationship("Job", back_populates="exam", passive_deletes=True)
    questions: Mapped[list["Question"]] = relationship("Question", back_populates="exam", passive_deletes=True)
    parse_errors: Mapped[list["ParseError"]] = relationship("ParseError", back_populates="exam", passive_deletes=True)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    exam_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exams.id", ondelete="SET NULL"), nullable=True
    )
    category: Mapped[DocumentCategory] = mapped_column(
        Enum(DocumentCategory, name="document_category_enum"),
        nullable=False,
        default=DocumentCategory.prova,
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status_enum"), nullable=False, default=JobStatus.pending
    )
    total_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parsed_ok: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parse_errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    exam: Mapped["Exam | None"] = relationship("Exam", back_populates="jobs")
    questions: Mapped[list["Question"]] = relationship("Question", back_populates="job")
    parse_error_records: Mapped[list["ParseError"]] = relationship(
        "ParseError", back_populates="job"
    )


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    exam_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exams.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[SectionType] = mapped_column(
        Enum(SectionType, name="section_type_enum"),
        nullable=False,
        default=SectionType.unknown,
    )
    question_type: Mapped[QuestionType] = mapped_column(
        Enum(QuestionType, name="question_type_enum"),
        nullable=False,
        default=QuestionType.unknown,
    )
    enunciado: Mapped[str] = mapped_column(Text, nullable=False)
    items: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    alternatives: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    gabarito: Mapped[str | None] = mapped_column(String(1), nullable=True)
    raw_block: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    enrichment: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    exam: Mapped["Exam"] = relationship("Exam", back_populates="questions")
    job: Mapped["Job"] = relationship("Job", back_populates="questions")


class ParseError(Base):
    __tablename__ = "parse_errors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    exam_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exams.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    raw_block: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    exam: Mapped["Exam"] = relationship("Exam", back_populates="parse_errors")
    job: Mapped["Job"] = relationship("Job", back_populates="parse_error_records")
