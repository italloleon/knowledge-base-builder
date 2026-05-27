"""Stage 1 — PDF to Markdown via Docling."""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ExtractionResult:
    markdown: str
    # {ref_key: png_bytes} — one entry per figure Docling detected in the PDF.
    # ref_key matches the alt-text in the markdown image tag, e.g. "picture-3".
    images: dict[str, bytes] = field(default_factory=dict)


def extract_markdown(pdf_path: Path) -> str:
    """Convert a PDF file to Markdown using Docling (text only, no images).

    Raises ValueError if the output is suspiciously short (< 100 chars).
    Kept for backwards-compatibility with the edital pipeline which doesn't
    need images.
    """
    return extract(pdf_path).markdown


def extract(pdf_path: Path) -> ExtractionResult:
    """Convert a PDF to Markdown and extract embedded figures as PNG bytes.

    Images are saved by index (0, 1, 2, …) and the markdown uses numbered
    placeholders ``<!-- image:0 -->``, ``<!-- image:1 -->``, etc. so parsers
    can map each placeholder to its saved file.

    Raises ValueError if the markdown output is suspiciously short.
    """
    import io as _io
    import re as _re

    from docling.datamodel.base_models import InputFormat  # noqa: PLC0415
    from docling.datamodel.pipeline_options import PdfPipelineOptions  # noqa: PLC0415
    from docling.document_converter import DocumentConverter, PdfFormatOption  # noqa: PLC0415

    artifacts_path = os.getenv("DOCLING_ARTIFACTS_PATH")
    pipeline_options = PdfPipelineOptions(
        **({"artifacts_path": artifacts_path} if artifacts_path else {}),
        generate_picture_images=True,
    )
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )
    result = converter.convert(str(pdf_path))

    # Default export uses <!-- image --> as placeholder for every figure.
    # Number them sequentially so parsers can reference them by index.
    counter = _re.compile(r"<!-- image -->")
    _idx = 0

    def _replace(m: _re.Match) -> str:  # noqa: ANN001
        nonlocal _idx
        out = f"<!-- image:{_idx} -->"
        _idx += 1
        return out

    md: str = counter.sub(_replace, result.document.export_to_markdown())

    if not md or len(md.strip()) < 100:
        raise ValueError(
            f"Extraction produced suspiciously short output: {len(md.strip() if md else '')} chars"
        )

    # Extract picture bytes using Docling v2 API: PictureItem.get_image(doc)
    images: dict[str, bytes] = {}
    for idx, pic in enumerate(getattr(result.document, "pictures", [])):
        try:
            pil_img = pic.get_image(result.document)
            if pil_img is None:
                continue
            buf = _io.BytesIO()
            pil_img.save(buf, format="PNG")
            images[str(idx)] = buf.getvalue()
        except Exception:  # noqa: BLE001
            pass

    return ExtractionResult(markdown=md, images=images)
