"""Stage 1 — PDF to text via PyMuPDF (no ML models required)."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ExtractionResult:
    markdown: str
    # {ref_key: png_bytes} — one entry per figure detected in the PDF.
    images: dict[str, bytes] = field(default_factory=dict)


def extract_markdown(pdf_path: Path) -> str:
    """Convert a PDF to text. Kept for compatibility with the edital pipeline."""
    return extract(pdf_path).markdown


def extract(pdf_path: Path) -> ExtractionResult:
    """Convert a PDF to plain text and extract embedded figures as PNG bytes.

    Text blocks and images are interleaved by their vertical position on each
    page, so image placeholders (<!-- image:N -->) appear near the question
    they belong to. The parser downstream handles the placeholders.

    Raises ValueError if the output is suspiciously short (< 100 chars).
    """
    import fitz  # pymupdf

    doc = fitz.open(str(pdf_path))
    parts: list[str] = []
    images: dict[str, bytes] = {}
    img_idx = 0

    for page in doc:
        # Collect text blocks: (x0, y0, x1, y1, text, block_no, block_type)
        # block_type 0 = text, 1 = image placeholder from PDF structure
        text_blocks: list[tuple[float, str]] = []
        for b in page.get_text("blocks"):
            if b[6] == 0 and b[4].strip():
                text_blocks.append((b[1], b[4].strip()))

        # Collect rasterised images with their top-left y coordinate
        image_blocks: list[tuple[float, str]] = []
        seen_xrefs: set[int] = set()
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)
            rects = page.get_image_rects(xref)
            if not rects:
                continue
            y0 = rects[0].y0
            try:
                pix = fitz.Pixmap(doc, xref)
                if pix.n > 4:  # CMYK/alpha → RGB
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                if pix.width < 10 or pix.height < 10:  # skip decorative dots/lines
                    continue
                images[str(img_idx)] = pix.tobytes("png")
                image_blocks.append((y0, f"<!-- image:{img_idx} -->"))
                img_idx += 1
            except Exception:
                pass

        # Interleave by vertical position and append to output
        for _, content in sorted(text_blocks + image_blocks, key=lambda x: x[0]):
            parts.append(content)

    md = "\n".join(parts)
    if len(md.strip()) < 100:
        raise ValueError(
            f"Extraction produced suspiciously short output: {len(md.strip())} chars"
        )
    return ExtractionResult(markdown=md, images=images)
