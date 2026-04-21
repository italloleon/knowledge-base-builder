"""Stage 1 — PDF to Markdown via Docling."""

import os
from pathlib import Path


def extract_markdown(pdf_path: Path) -> str:
    """Convert a PDF file to Markdown using Docling.

    Raises ValueError if the output is suspiciously short (< 100 chars).
    """
    from docling.datamodel.base_models import InputFormat  # noqa: PLC0415
    from docling.datamodel.pipeline_options import PdfPipelineOptions  # noqa: PLC0415
    from docling.document_converter import DocumentConverter, PdfFormatOption  # noqa: PLC0415

    artifacts_path = os.getenv("DOCLING_ARTIFACTS_PATH")
    pipeline_options = PdfPipelineOptions(
        **({"artifacts_path": artifacts_path} if artifacts_path else {})
    )
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )
    result = converter.convert(str(pdf_path))
    md: str = result.document.export_to_markdown()

    if not md or len(md.strip()) < 100:
        raise ValueError(
            f"Extraction produced suspiciously short output: {len(md.strip() if md else '')} chars"
        )

    return md
