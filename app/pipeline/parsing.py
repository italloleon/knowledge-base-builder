"""Stage 3 — Deterministic parser: clean markdown → structured question records."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models import QuestionType, SectionType
from app.pipeline.preprocessing import PreprocessResult

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Question boundary patterns — same set as preprocessing._QUESTION_NUMBER_PATTERNS
_Q_NUMBER_PATTERNS = [
    re.compile(r"^\*{0,2}(0?\d{1,2}|100)\*{0,2}\s*$"),
    re.compile(r"^(?:\*\*)?QUEST[ÃA]O\s+(?:\*\*\s*)?(0?\d{1,2}|100)", re.IGNORECASE),
    re.compile(r"^#{1,3}\s+(0?\d{1,2}|100)\s*$"),
]


def _q_number_from_line(line: str) -> int | None:
    stripped = line.strip()
    for pat in _Q_NUMBER_PATTERNS:
        m = pat.match(stripped)
        if m:
            num = int(m.group(1))
            if 1 <= num <= 200:
                return num
    return None

# Alternative patterns (tried in order; we record which one matched)
_ALT_PAREN_BOTH = re.compile(r"^\s*\(([A-E])\)\s+(.+)", re.DOTALL)   # (A) text
_ALT_PAREN_RIGHT = re.compile(r"^\s*([A-E])\)\s+(.+)", re.DOTALL)    # A) text
_ALT_DOT = re.compile(r"^\s*([A-E])\.\s+(.+)", re.DOTALL)            # A. text

# Roman numeral item line: I - text / II. text / III – text
_ROMAN_ITEM = re.compile(
    r"^\s*(I{1,3}|IV|V?I{0,3}|VI{0,3}|IX|X)\s*[-–.]\s+(.+)",
    re.IGNORECASE,
)

# True/False item: ( ) text  or (V) text or (F) text
_TRUE_FALSE_ITEM = re.compile(r"^\s*\(\s*[VFvf]?\s*\)\s+(.+)")

# Association item: digit. text  (before alternatives section)
_ASSOC_ITEM = re.compile(r"^\s*(\d+)\.\s+(.+)")

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _split_into_blocks(text: str) -> list[tuple[int, str]]:
    """Split clean markdown text into (question_number, block_text) tuples."""
    lines = text.splitlines()
    blocks: list[tuple[int, str]] = []
    current_number: int | None = None
    current_lines: list[str] = []

    for line in lines:
        num = _q_number_from_line(line)
        if num is not None:
            if current_number is not None:
                blocks.append((current_number, "\n".join(current_lines).strip()))
            current_number = num
            current_lines = [line]
            continue

        if current_number is not None:
            current_lines.append(line)
        # Lines before the first question number are discarded (preamble)

    if current_number is not None and current_lines:
        blocks.append((current_number, "\n".join(current_lines).strip()))

    return blocks


def _extract_alternatives(lines: list[str]) -> tuple[dict[str, str], str, bool]:
    """Return (alternatives_dict, pattern_name, ambiguous).

    Tries patterns in priority order. ambiguous=True when multiple patterns
    match on different lines (signals lower confidence).
    """
    alt: dict[str, str] = {}
    patterns_used: set[str] = set()

    for line in lines:
        for pattern, name in [
            (_ALT_PAREN_BOTH, "paren_both"),
            (_ALT_PAREN_RIGHT, "paren_right"),
            (_ALT_DOT, "dot"),
        ]:
            m = pattern.match(line)
            if m:
                label = m.group(1).upper()
                text = m.group(2).strip()
                if label not in alt:  # first match wins per label
                    alt[label] = text
                    patterns_used.add(name)
                break  # stop checking patterns for this line

    ambiguous = len(patterns_used) > 1
    pattern_name = next(iter(patterns_used)) if len(patterns_used) == 1 else "mixed"
    return alt, pattern_name, ambiguous


def _extract_roman_items(lines: list[str]) -> list[dict]:
    items = []
    for line in lines:
        m = _ROMAN_ITEM.match(line)
        if m:
            items.append({"label": m.group(1).upper(), "text": m.group(2).strip()})
    return items


def _extract_true_false_items(lines: list[str]) -> list[dict]:
    items = []
    for line in lines:
        m = _TRUE_FALSE_ITEM.match(line)
        if m:
            items.append({"label": "( )", "text": m.group(1).strip()})
    return items


def _extract_association_items(lines: list[str]) -> list[dict]:
    """Extract numbered association items that appear before alternatives."""
    items = []
    in_alt_section = False
    for line in lines:
        # Once we hit alternatives, stop collecting association items
        if any(
            p.match(line)
            for p in [_ALT_PAREN_BOTH, _ALT_PAREN_RIGHT, _ALT_DOT]
        ):
            in_alt_section = True
        if in_alt_section:
            continue
        m = _ASSOC_ITEM.match(line)
        if m:
            items.append({"label": m.group(1), "text": m.group(2).strip()})
    return items


def _infer_question_type(
    lines: list[str],
    alternatives: dict[str, str],
) -> QuestionType:
    # Check for roman numeral items
    roman_count = sum(1 for line in lines if _ROMAN_ITEM.match(line))
    if roman_count >= 2:
        return QuestionType.roman_numeral

    # Check for true/false items
    tf_count = sum(1 for line in lines if _TRUE_FALSE_ITEM.match(line))
    if tf_count >= 2:
        return QuestionType.true_false

    # Check for association: numbered items before alternatives that reference numbers
    assoc_items = _extract_association_items(lines)
    if len(assoc_items) >= 2:
        # Verify that at least one alternative references a digit sequence
        alt_text = " ".join(alternatives.values())
        if re.search(r"\b\d\b.*\b\d\b", alt_text):
            return QuestionType.association

    return QuestionType.simple


def _compute_confidence(
    alternatives: dict[str, str],
    ambiguous_pattern: bool,
    question_type: QuestionType,
    enunciado: str,
) -> float:
    score = 1.0

    if ambiguous_pattern:
        score -= 0.2

    if question_type == QuestionType.unknown:
        score -= 0.1

    # Penalise if fewer than 5 alternatives found
    if len(alternatives) < 5:
        score -= 0.3

    # Penalise if enunciado looks truncated
    if len(enunciado.strip()) < 20:
        score -= 0.2

    return max(0.0, min(1.0, score))


def _build_enunciado(lines: list[str], alternatives: dict[str, str]) -> str:
    """Collect all lines that are neither the question-number line nor alternatives."""
    stem_lines: list[str] = []

    for line in lines:
        # Skip the question number line itself
        if _q_number_from_line(line) is not None:
            continue

        # Skip lines that are alternatives
        is_alt = False
        for pattern in [_ALT_PAREN_BOTH, _ALT_PAREN_RIGHT, _ALT_DOT]:
            m = pattern.match(line)
            if m and m.group(1).upper() in "ABCDE":
                is_alt = True
                break
        if is_alt:
            continue

        stem_lines.append(line)

    return "\n".join(stem_lines).strip()


# ---------------------------------------------------------------------------
# Vignette detection and injection
# ---------------------------------------------------------------------------


def _inject_vignettes(questions: list[ParsedQuestion]) -> list[ParsedQuestion]:
    """Detect shared clinical vignettes and prepend them to affected questions.

    A vignette is detected when consecutive questions share a text block that
    appears before the first question number in that group — or when a question's
    enunciado is very short and the previous question has a clearly shared context
    paragraph at the end of its enunciado.
    """
    # Heuristic: if a question's enunciado is < 50 chars and the immediately
    # preceding question's enunciado ends with a paragraph that looks like
    # shared context (no alternatives inside it), prepend that paragraph.
    result = list(questions)
    for i in range(1, len(result)):
        q = result[i]
        prev = result[i - 1]

        if len(q.enunciado.strip()) < 50:
            # Attempt to extract a vignette from prev enunciado
            # Split prev enunciado on double-newlines and take the last paragraph
            paragraphs = [p.strip() for p in prev.enunciado.split("\n\n") if p.strip()]
            if len(paragraphs) >= 2:
                vignette = paragraphs[-1]
                # Only inject if the vignette paragraph is substantial
                if len(vignette) > 60:
                    result[i] = ParsedQuestion(
                        number=q.number,
                        section=q.section,
                        question_type=q.question_type,
                        enunciado=f"{vignette}\n\n{q.enunciado}",
                        items=q.items,
                        alternatives=q.alternatives,
                        gabarito=q.gabarito,
                        raw_block=q.raw_block,
                        confidence=q.confidence,
                    )
    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def parse_questions(preprocess_result: PreprocessResult) -> ParseResult:
    """Parse preprocessed markdown into structured question records.

    Never raises. Any block that cannot be parsed is recorded as a ParseFailure.
    """
    result = ParseResult()
    blocks = _split_into_blocks(preprocess_result.clean_text)

    for number, raw_block in blocks:
        try:
            _parse_block(number, raw_block, preprocess_result.section_map, result)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(
                ParseFailure(raw_block=raw_block, reason=str(exc))
            )

    result.questions = _inject_vignettes(result.questions)
    return result


def _parse_block(
    number: int,
    raw_block: str,
    section_map: dict[int, str],
    result: ParseResult,
) -> None:
    """Parse a single question block and append to result.questions or result.errors."""
    lines = raw_block.splitlines()

    # Extract alternatives
    alternatives, _pattern_name, ambiguous = _extract_alternatives(lines)

    # Infer question type
    question_type = _infer_question_type(lines, alternatives)

    # Extract items based on type
    items: list[dict] | None = None
    if question_type == QuestionType.roman_numeral:
        items = _extract_roman_items(lines) or None
    elif question_type == QuestionType.true_false:
        items = _extract_true_false_items(lines) or None
    elif question_type == QuestionType.association:
        items = _extract_association_items(lines) or None

    # Build enunciado
    enunciado = _build_enunciado(lines, alternatives)

    # Determine section
    raw_section = section_map.get(number, "unknown")
    try:
        section = SectionType(raw_section)
    except ValueError:
        section = SectionType.unknown

    # Compute confidence
    confidence = _compute_confidence(
        alternatives=alternatives,
        ambiguous_pattern=ambiguous,
        question_type=question_type,
        enunciado=enunciado,
    )

    # If we have absolutely no alternatives and no enunciado, record as error
    if not alternatives and not enunciado.strip():
        result.errors.append(
            ParseFailure(
                raw_block=raw_block,
                reason=f"Question {number}: no alternatives and no enunciado found",
            )
        )
        return

    result.questions.append(
        ParsedQuestion(
            number=number,
            section=section.value,
            question_type=question_type.value,
            enunciado=enunciado,
            items=items,
            alternatives=alternatives,
            gabarito=None,
            raw_block=raw_block,
            confidence=confidence,
        )
    )
