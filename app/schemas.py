import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, EmailStr, StringConstraints, field_validator

from app.models import DocumentCategory, JobStatus, OpinionTarget, QuestionType, SectionType


# --------------------------------------------------------------------------- #
# User                                                                          #
# --------------------------------------------------------------------------- #


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str
    password: Annotated[str, StringConstraints(min_length=8)]


class UserUpdate(BaseModel):
    full_name: str | None = None
    email: EmailStr | None = None
    password: Annotated[str, StringConstraints(min_length=8)] | None = None


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# --------------------------------------------------------------------------- #
# Auth tokens                                                                   #
# --------------------------------------------------------------------------- #


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# --------------------------------------------------------------------------- #
# Ingestion                                                                     #
# --------------------------------------------------------------------------- #


class IngestURLRequest(BaseModel):
    url: str
    category: DocumentCategory

    @field_validator("url")
    @classmethod
    def validate_url_scheme(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must use http or https scheme")
        return v


class IngestResponse(BaseModel):
    job_id: uuid.UUID
    exam_id: uuid.UUID | None
    edital_id: uuid.UUID | None = None


# --------------------------------------------------------------------------- #
# Job                                                                           #
# --------------------------------------------------------------------------- #


class JobResponse(BaseModel):
    id: uuid.UUID
    exam_id: uuid.UUID | None
    edital_id: uuid.UUID | None
    category: DocumentCategory
    status: JobStatus
    total_found: int
    parsed_ok: int
    parse_errors: int
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# --------------------------------------------------------------------------- #
# Edital                                                                        #
# --------------------------------------------------------------------------- #


class EditalResponse(BaseModel):
    id: uuid.UUID
    filename: str
    file_hash: str
    uploaded_by: UserResponse | None
    numero_edital: str | None
    ano: int | None
    edition_name: str | None
    organizadora: str | None
    instituicao_gestora: str | None
    modalidade: str | None
    total_questoes_gerais: int | None
    total_questoes_especificas: int | None
    percentual_minimo_aprovacao: float | None
    bolsa_mensal: float | None
    data_inicio_programas: str | None
    contato_email: str | None
    contato_telefone: str | None
    url_enare: str | None
    cronograma: list | None
    vagas: list | None
    instituicoes: list | None
    knowledge_areas: list | None
    created_at: datetime

    model_config = {"from_attributes": True}


class EditalLinkRequest(BaseModel):
    edital_id: uuid.UUID


class EditalEnrichResponse(BaseModel):
    message: str
    job_id: str


# --------------------------------------------------------------------------- #
# Exam                                                                          #
# --------------------------------------------------------------------------- #


class ExamResponse(BaseModel):
    id: uuid.UUID
    filename: str
    file_hash: str
    edital_id: uuid.UUID | None
    uploaded_by: UserResponse | None
    question_count: int
    enriched_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class EnrichRequest(BaseModel):
    mode: Literal["missing", "all"] = "missing"
    provider: Literal["ollama", "gemini"] | None = None


class EnrichResponse(BaseModel):
    message: str
    queued: int


# --------------------------------------------------------------------------- #
# Question                                                                      #
# --------------------------------------------------------------------------- #


class QuestionSummary(BaseModel):
    id: uuid.UUID
    exam_id: uuid.UUID
    job_id: uuid.UUID
    number: int
    section: SectionType
    question_type: QuestionType
    enunciado: str
    items: list[dict] | None
    alternatives: dict
    gabarito: str | None
    confidence: float
    enrichment: dict | None
    explanation: dict | None
    explanation_flagged: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class QuestionDetail(QuestionSummary):
    raw_block: str


class PaginatedQuestions(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[QuestionSummary]


# --------------------------------------------------------------------------- #
# Parse errors                                                                  #
# --------------------------------------------------------------------------- #


class ParseErrorResponse(BaseModel):
    id: uuid.UUID
    exam_id: uuid.UUID
    job_id: uuid.UUID
    raw_block: str
    reason: str
    created_at: datetime

    model_config = {"from_attributes": True}


# --------------------------------------------------------------------------- #
# Import                                                                        #
# --------------------------------------------------------------------------- #


class ImportResponse(BaseModel):
    exams_created: int
    exams_existing: int
    questions_created: int
    questions_skipped: int
    questions_enrichment_updated: int


class FullImportResponse(BaseModel):
    editais_created: int
    editais_existing: int
    exams_created: int
    exams_existing: int
    questions_created: int
    questions_skipped: int
    questions_enrichment_updated: int


# --------------------------------------------------------------------------- #
# Gabarito                                                                      #
# --------------------------------------------------------------------------- #


class GabaritoCaderno(BaseModel):
    name: str
    answers: dict[str, str | None]  # str keys (JSON), None = annulled
    answer_count: int
    annulled: list[int]
    warnings: list[str] = []


class GabaritoParseResponse(BaseModel):
    cadernos: list[GabaritoCaderno]


class ExplainRequest(BaseModel):
    mode: Literal["missing", "all"] = "missing"
    provider: Literal["ollama", "gemini"] | None = None


class ExplainResponse(BaseModel):
    message: str
    queued: int


class ApplyGabaritoRequest(BaseModel):
    answers: dict[str, str | None]


class ApplyGabaritoResponse(BaseModel):
    updated: int
    annulled: int


# --------------------------------------------------------------------------- #
# Opinion                                                                       #
# --------------------------------------------------------------------------- #


class OpinionCreate(BaseModel):
    target: OpinionTarget = OpinionTarget.question
    body: Annotated[str, StringConstraints(min_length=1, max_length=5000)]


class OpinionUpdate(BaseModel):
    body: Annotated[str, StringConstraints(min_length=1, max_length=5000)]


class OpinionResponse(BaseModel):
    id: uuid.UUID
    question_id: uuid.UUID
    user_id: uuid.UUID
    author: UserResponse
    target: OpinionTarget
    body: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# --------------------------------------------------------------------------- #
# Health                                                                        #
# --------------------------------------------------------------------------- #


class HealthLive(BaseModel):
    status: str = "ok"


class HealthReady(BaseModel):
    status: str
    database: str
    redis: str
