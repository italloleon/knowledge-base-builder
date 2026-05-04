import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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


class OpinionTarget(str, enum.Enum):
    question = "question"
    alternative_a = "alternative_a"
    alternative_b = "alternative_b"
    alternative_c = "alternative_c"
    alternative_d = "alternative_d"
    alternative_e = "alternative_e"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(256), nullable=False, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(256), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        "RefreshToken", back_populates="user", passive_deletes=True
    )
    opinions: Mapped[list["QuestionOpinion"]] = relationship(
        "QuestionOpinion", back_populates="user", passive_deletes=True
    )
    uploaded_exams: Mapped[list["Exam"]] = relationship(
        "Exam", back_populates="uploaded_by", foreign_keys="Exam.uploaded_by_id"
    )
    uploaded_editais: Mapped[list["Edital"]] = relationship(
        "Edital", back_populates="uploaded_by", foreign_keys="Edital.uploaded_by_id"
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="refresh_tokens")


class Edital(Base):
    __tablename__ = "editais"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Tier-1 scalar fields extracted via regex
    numero_edital: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ano: Mapped[int | None] = mapped_column(Integer, nullable=True)
    edition_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    organizadora: Mapped[str | None] = mapped_column(String(128), nullable=True)
    instituicao_gestora: Mapped[str | None] = mapped_column(String(128), nullable=True)
    modalidade: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_questoes_gerais: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_questoes_especificas: Mapped[int | None] = mapped_column(Integer, nullable=True)
    percentual_minimo_aprovacao: Mapped[float | None] = mapped_column(Float, nullable=True)
    bolsa_mensal: Mapped[float | None] = mapped_column(Float, nullable=True)
    data_inicio_programas: Mapped[str | None] = mapped_column(String(128), nullable=True)
    contato_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    contato_telefone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    url_enare: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Tier-2 JSONB fields extracted via LLM from Annexes
    cronograma: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    vagas: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    instituicoes: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    knowledge_areas: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("file_hash", name="uq_editais_file_hash"),)

    uploaded_by: Mapped["User | None"] = relationship(
        "User", back_populates="uploaded_editais", foreign_keys=[uploaded_by_id]
    )
    exams: Mapped[list["Exam"]] = relationship(
        "Exam", back_populates="edital", passive_deletes=True
    )
    jobs: Mapped[list["Job"]] = relationship(
        "Job", back_populates="edital", passive_deletes=True
    )


class Exam(Base):
    __tablename__ = "exams"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    edital_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("editais.id", ondelete="SET NULL"),
        nullable=True,
    )
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("file_hash", name="uq_exams_file_hash"),)

    uploaded_by: Mapped["User | None"] = relationship(
        "User", back_populates="uploaded_exams", foreign_keys=[uploaded_by_id]
    )
    edital: Mapped["Edital | None"] = relationship("Edital", back_populates="exams")
    jobs: Mapped[list["Job"]] = relationship("Job", back_populates="exam", passive_deletes=True)
    questions: Mapped[list["Question"]] = relationship(
        "Question", back_populates="exam", passive_deletes=True
    )
    parse_errors: Mapped[list["ParseError"]] = relationship(
        "ParseError", back_populates="exam", passive_deletes=True
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    exam_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exams.id", ondelete="SET NULL"), nullable=True
    )
    edital_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editais.id", ondelete="SET NULL"), nullable=True
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
    edital: Mapped["Edital | None"] = relationship("Edital", back_populates="jobs")
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
    explanation: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    explanation_flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    exam: Mapped["Exam"] = relationship("Exam", back_populates="questions")
    job: Mapped["Job"] = relationship("Job", back_populates="questions")
    opinions: Mapped[list["QuestionOpinion"]] = relationship(
        "QuestionOpinion", back_populates="question", passive_deletes=True
    )


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


class QuestionOpinion(Base):
    __tablename__ = "question_opinions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    target: Mapped[OpinionTarget] = mapped_column(
        Enum(OpinionTarget, name="opinion_target_enum"),
        nullable=False,
        default=OpinionTarget.question,
    )
    body: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        info={"check": "char_length(body) <= 5000"},
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint("char_length(body) <= 5000", name="ck_opinion_body_length"),
    )

    question: Mapped["Question"] = relationship("Question", back_populates="opinions")
    user: Mapped["User"] = relationship("User", back_populates="opinions")
