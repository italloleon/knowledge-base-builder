"""Stage 2 — Strip noise and extract section boundaries."""

import re
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class PreprocessResult:
    clean_text: str
    # Maps question_number (int) → section name string
    section_map: dict[int, str] = field(default_factory=dict)


# Patterns for section headings (case-insensitive)
_SECTION_GERAIS = re.compile(
    r"conhecimentos?\s+gerais", re.IGNORECASE | re.UNICODE
)
_SECTION_ESPECIFICOS = re.compile(
    r"conhecimentos?\s+espec[ií]ficos?", re.IGNORECASE | re.UNICODE
)

# Question number patterns — all formats observed in ENARE/FGV PDFs
_QUESTION_NUMBER_PATTERNS = [
    # Bare number or bold markdown: "1", "**1**", "01", "**01**"
    re.compile(r"^\*{0,2}(0?\d{1,2}|100)\*{0,2}\s*$"),
    # QUESTÃO prefix (most common in ENARE): "QUESTÃO 1", "**QUESTÃO** 01"
    re.compile(r"^(?:\*\*)?QUEST[ÃA]O\s+(?:\*\*\s*)?(0?\d{1,2}|100)", re.IGNORECASE | re.UNICODE),
    # Markdown heading: "# 1", "## 01"
    re.compile(r"^#{1,3}\s+(0?\d{1,2}|100)\s*$"),
]


def _question_number(line: str) -> int | None:
    """Return the question number if this line marks a question boundary, else None."""
    stripped = line.strip()
    for pattern in _QUESTION_NUMBER_PATTERNS:
        m = pattern.match(stripped)
        if m:
            num = int(m.group(1))
            if 1 <= num <= 200:
                return num
    return None


# Typical ENARE/FGV noise patterns
_NOISE_PATTERNS = [
    re.compile(r"^\s*ENARE\b.*$", re.IGNORECASE),
    re.compile(r"^\s*FGV\b.*$", re.IGNORECASE),
    re.compile(r"^\s*\d{4}\s*$"),                          # standalone 4-digit year
    re.compile(r"^\s*P[áa]gina\s+\d+.*$", re.IGNORECASE),
    re.compile(r"^\s*[-–]\s*\d{1,3}\s*[-–]\s*$"),         # page number like "- 5 -"
    re.compile(r"^\s*ENFER(MAGEM)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*PROVA\s+[A-Z]\s*$", re.IGNORECASE),
    re.compile(r"^\s*CADERNO\s+DE\s+PROVA\s*$", re.IGNORECASE),
    re.compile(r"^\s*GABARITO\s*$", re.IGNORECASE),
    re.compile(r"^\s*Residência\s+em\s+Enfermagem\s*$", re.IGNORECASE),
]


def _detect_repeating_lines(lines: list[str], min_repeats: int = 3) -> set[str]:
    """Return stripped lines that appear at least min_repeats times."""
    stripped = [line.strip() for line in lines if line.strip()]
    counter = Counter(stripped)
    return {line for line, count in counter.items() if count >= min_repeats}


def _is_noise(line: str, repeating: set[str]) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped in repeating:
        return True
    return any(p.match(stripped) for p in _NOISE_PATTERNS)


def _section_from_line(line: str) -> str | None:
    """Return a section name if the line is a section heading, else None."""
    if _SECTION_GERAIS.search(line):
        return "conhecimentos_gerais"
    if _SECTION_ESPECIFICOS.search(line):
        return "conhecimentos_especificos"
    return None


def _update_section_map(
    section_map: dict[int, str],
    num: int,
    last: int,
    section: str,
) -> int:
    for n in range(last + 1, num + 1):
        if n not in section_map:
            section_map[n] = section
    return num


def preprocess(markdown: str) -> PreprocessResult:
    """Strip repeating headers/footers and identify section boundaries."""
    lines = markdown.splitlines()
    repeating = _detect_repeating_lines(lines, min_repeats=3)

    clean_lines: list[str] = []
    section_map: dict[int, str] = {}
    current_section = "unknown"
    last_question_number = 0

    for line in lines:
        if _is_noise(line, repeating):
            continue

        section = _section_from_line(line)
        if section is not None:
            current_section = section
            continue  # heading line is metadata, not content

        num = _question_number(line)
        if num is not None:
            last_question_number = _update_section_map(
                section_map, num, last_question_number, current_section
            )

        clean_lines.append(line)

    return PreprocessResult(
        clean_text="\n".join(clean_lines),
        section_map=section_map,
    )
