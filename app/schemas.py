import uuid
from datetime import datetime

from pydantic import BaseModel, HttpUrl, field_validator

from app.models import DocumentCategory, JobStatus, QuestionType, SectionType


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


# --------------------------------------------------------------------------- #
# Job                                                                           #
# --------------------------------------------------------------------------- #


class JobResponse(BaseModel):
    id: uuid.UUID
    exam_id: uuid.UUID | None
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
# Exam                                                                          #
# --------------------------------------------------------------------------- #


class ExamResponse(BaseModel):
    id: uuid.UUID
    filename: str
    file_hash: str
    question_count: int
    enriched_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class EnrichRequest(BaseModel):
    mode: str = "missing"  # "missing" | "all"
    provider: str | None = None  # None = use ENRICHMENT_PROVIDER from config; "ollama" | "gemini"


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
# Health                                                                        #
# --------------------------------------------------------------------------- #


class HealthLive(BaseModel):
    status: str = "ok"


class HealthReady(BaseModel):
    status: str
    database: str
    redis: str
