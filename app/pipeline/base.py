"""Base classes and shared data models for the document parsing pipeline."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class PreprocessResult:
    clean_text: str
    section_map: dict[int, str] = field(default_factory=dict)


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
