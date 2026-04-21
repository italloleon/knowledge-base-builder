from app.models import DocumentCategory
from app.pipeline.base import DocumentParser


def get_parser(category: DocumentCategory) -> DocumentParser:
    if category == DocumentCategory.prova:
        from app.pipeline.parsers.enare import ENAREParser
        return ENAREParser()
    if category == DocumentCategory.edital:
        from app.pipeline.parsers.edital import EditalParser
        return EditalParser()
    raise ValueError(f"No parser registered for category: {category}")
