"""Edital (exam notice) parser — not yet implemented."""

from app.pipeline.base import DocumentParser, ParseResult, PreprocessResult


class EditalParser(DocumentParser):
    """Parser for ENARE edital (notice/announcement) PDFs."""

    def preprocess(self, markdown: str) -> PreprocessResult:
        raise NotImplementedError("EditalParser is not yet implemented")

    def parse(self, preprocess_result: PreprocessResult) -> ParseResult:
        raise NotImplementedError("EditalParser is not yet implemented")
