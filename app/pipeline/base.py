"""Base classes and shared data models for the document parsing pipeline."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class PreprocessResult:
    clean_text: str
    section_map: dict[int, str] = field(default_factory=dict)


@dataclass
class EditalData:
    """Structured data extracted from an Edital (exam notice) PDF."""

    # Scalar fields — extracted via regex from the main document body
    numero_edital: str | None = None
    ano: int | None = None
    edition_name: str | None = None
    organizadora: str | None = None
    instituicao_gestora: str | None = None
    modalidade: str | None = None
    total_questoes_gerais: int | None = None
    total_questoes_especificas: int | None = None
    percentual_minimo_aprovacao: float | None = None
    bolsa_mensal: float | None = None
    data_inicio_programas: str | None = None
    contato_email: str | None = None
    contato_telefone: str | None = None
    url_enare: str | None = None

    # JSONB fields — extracted via LLM from Annexes when present in the PDF
    # cronograma: [{"evento": str, "data_inicio": str, "data_fim": str | null}]
    cronograma: list[dict] = field(default_factory=list)
    # vagas: [{"profissao": str, "instituicao": str, "cidade": str, "estado": str,
    #           "programa": str, "vagas_ampla": int, "vagas_reservadas": dict}]
    vagas: list[dict] = field(default_factory=list)
    # instituicoes: [{"nome": str, "sigla": str, "cidade": str, "estado": str,
    #                 "programas": [str]}]
    instituicoes: list[dict] = field(default_factory=list)
    # knowledge_areas: [{"profissao": str,
    #                     "gerais": [{"area": str, "topicos": [str]}],
    #                     "especificos": [{"area": str, "topicos": [str]}]}]
    knowledge_areas: list[dict] = field(default_factory=list)


@dataclass
class ParsedQuestion:
    number: int
    section: str
    question_type: str
    enunciado: str
    items: list[dict] | None
    alternatives: dict[str, str]
    gabarito: str | None
    raw_block: str
    confidence: float


@dataclass
class ParseFailure:
    raw_block: str
    reason: str


@dataclass
class ParseResult:
    questions: list[ParsedQuestion] = field(default_factory=list)
    errors: list[ParseFailure] = field(default_factory=list)


class DocumentParser(ABC):
    """Abstract base for document parsers.

    Subclasses implement preprocess() and parse() for a specific document type.
    Call run() to execute both stages in sequence.
    """

    @abstractmethod
    def preprocess(self, markdown: str) -> PreprocessResult: ...

    @abstractmethod
    def parse(self, preprocess_result: PreprocessResult) -> ParseResult: ...

    def run(self, markdown: str) -> ParseResult:
        return self.parse(self.preprocess(markdown))
