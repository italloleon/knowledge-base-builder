"""ENARE/FGV exam parser — handles multi-column nursing residency exam PDFs."""

from __future__ import annotations

import re
from collections import Counter

from app.models import QuestionType, SectionType
from app.pipeline.base import (
    DocumentParser,
    ParsedQuestion,
    ParseFailure,
    ParseResult,
    PreprocessResult,
)

# ---------------------------------------------------------------------------
# Shared question boundary patterns (used in both stages)
# ---------------------------------------------------------------------------

_QUESTION_NUMBER_PATTERNS = [
    re.compile(r"^\*{0,2}(0?\d{1,2}|100)\*{0,2}\s*$"),
    re.compile(r"^(?:\*\*)?QUEST[ÃA]O\s+(?:\*\*\s*)?(0?\d{1,2}|100)", re.IGNORECASE | re.UNICODE),
    re.compile(r"^#{1,3}\s+(0?\d{1,2}|100)\s*$"),
]

# ---------------------------------------------------------------------------
# Preprocessing patterns
# ---------------------------------------------------------------------------

_SECTION_GERAIS = re.compile(r"conhecimentos?\s+gerais", re.IGNORECASE | re.UNICODE)
_SECTION_ESPECIFICOS = re.compile(
    r"conhecimentos?\s+espec[ií]ficos?", re.IGNORECASE | re.UNICODE
)

_NOISE_PATTERNS = [
    re.compile(r"^\s*ENARE\b.*$", re.IGNORECASE),
    re.compile(r"^\s*FGV\b.*$", re.IGNORECASE),
    re.compile(r"^\s*\d{4}\s*$"),
    re.compile(r"^\s*P[áa]gina\s+\d+.*$", re.IGNORECASE),
    re.compile(r"^\s*[-–]\s*\d{1,3}\s*[-–]\s*$"),
    re.compile(r"^\s*ENFER(MAGEM)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*PROVA\s+[A-Z]\s*$", re.IGNORECASE),
    re.compile(r"^\s*CADERNO\s+DE\s+PROVA\s*$", re.IGNORECASE),
    re.compile(r"^\s*GABARITO\s*$", re.IGNORECASE),
    re.compile(r"^\s*Residência\s+em\s+Enfermagem\s*$", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Parsing patterns
# ---------------------------------------------------------------------------

_ALT_PAREN_BOTH = re.compile(r"^\s*(?:-\s*)?\(([A-E])\)\s+(.+)", re.DOTALL)
_ALT_PAREN_RIGHT = re.compile(r"^\s*([A-E])\)\s+(.+)", re.DOTALL)
_ALT_DOT = re.compile(r"^\s*([A-E])\.\s+(.+)", re.DOTALL)

_ROMAN_ITEM = re.compile(
    r"^\s*(I{1,3}|IV|IX|VI{0,3}|X)\s*[-–.]\s+(.+)",
    re.IGNORECASE,
)
_TRUE_FALSE_ITEM = re.compile(r"^\s*\(\s*[VFvf]?\s*\)\s+(.+)")
_ASSOC_ITEM = re.compile(r"^\s*(\d+)\.\s+(.+)")


class ENAREParser(DocumentParser):
    """Parser for ENARE/FGV nursing residency exam PDFs."""

    # ------------------------------------------------------------------
    # Stage 1 — preprocess
    # ------------------------------------------------------------------

    def preprocess(self, markdown: str) -> PreprocessResult:
        lines = markdown.splitlines()
        repeating = self._detect_repeating_lines(lines)

        clean_lines: list[str] = []
        section_map: dict[int, str] = {}
        current_section = "unknown"
        last_question_number = 0

        for line in lines:
            if self._is_noise(line, repeating):
                continue

            section = self._section_from_line(line)
            if section is not None:
                current_section = section
                continue

            num = self._question_number(line)
            if num is not None:
                last_question_number = self._update_section_map(
                    section_map, num, last_question_number, current_section
                )

            clean_lines.append(line)

        return PreprocessResult(
            clean_text="\n".join(clean_lines),
            section_map=section_map,
        )

    def _detect_repeating_lines(self, lines: list[str], min_repeats: int = 3) -> set[str]:
        stripped = [l.strip() for l in lines if l.strip()]
        counter = Counter(stripped)
        return {line for line, count in counter.items() if count >= min_repeats}

    def _is_noise(self, line: str, repeating: set[str]) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        if stripped in repeating:
            return True
        return any(p.match(stripped) for p in _NOISE_PATTERNS)

    def _section_from_line(self, line: str) -> str | None:
        if _SECTION_GERAIS.search(line):
            return "conhecimentos_gerais"
        if _SECTION_ESPECIFICOS.search(line):
            return "conhecimentos_especificos"
        return None

    def _update_section_map(
        self,
        section_map: dict[int, str],
        num: int,
        last: int,
        section: str,
    ) -> int:
        for n in range(last + 1, num + 1):
            if n not in section_map:
                section_map[n] = section
        return num

    # ------------------------------------------------------------------
    # Stage 2 — parse
    # ------------------------------------------------------------------

    def parse(self, preprocess_result: PreprocessResult) -> ParseResult:
        result = ParseResult()
        blocks = self._split_into_blocks(preprocess_result.clean_text)

        for number, raw_block in blocks:
            try:
                self._parse_block(number, raw_block, preprocess_result.section_map, result)
            except Exception as exc:  # noqa: BLE001
                result.errors.append(ParseFailure(raw_block=raw_block, reason=str(exc)))

        result.questions = self._inject_vignettes(result.questions)
        return result

    def _question_number(self, line: str) -> int | None:
        stripped = line.strip()
        for pattern in _QUESTION_NUMBER_PATTERNS:
            m = pattern.match(stripped)
            if m:
                num = int(m.group(1))
                if 1 <= num <= 200:
                    return num
        return None

    def _split_into_blocks(self, text: str) -> list[tuple[int, str]]:
        lines = text.splitlines()
        blocks: list[tuple[int, str]] = []
        current_number: int | None = None
        current_lines: list[str] = []

        for line in lines:
            num = self._question_number(line)
            if num is not None:
                if current_number is not None:
                    blocks.append((current_number, "\n".join(current_lines).strip()))
                current_number = num
                current_lines = [line]
                continue
            if current_number is not None:
                current_lines.append(line)

        if current_number is not None and current_lines:
            blocks.append((current_number, "\n".join(current_lines).strip()))

        return blocks

    def _extract_alternatives(self, lines: list[str]) -> tuple[dict[str, str], str, bool]:
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
                    if label not in alt:
                        alt[label] = text
                        patterns_used.add(name)
                    break

        ambiguous = len(patterns_used) > 1
        pattern_name = next(iter(patterns_used)) if len(patterns_used) == 1 else "mixed"
        return alt, pattern_name, ambiguous

    def _extract_roman_items(self, lines: list[str]) -> list[dict]:
        items = []
        for line in lines:
            m = _ROMAN_ITEM.match(line)
            if m:
                items.append({"label": m.group(1).upper(), "text": m.group(2).strip()})
        return items

    def _extract_true_false_items(self, lines: list[str]) -> list[dict]:
        items = []
        for line in lines:
            m = _TRUE_FALSE_ITEM.match(line)
            if m:
                items.append({"label": "( )", "text": m.group(1).strip()})
        return items

    def _extract_association_items(self, lines: list[str]) -> list[dict]:
        items = []
        in_alt_section = False
        for line in lines:
            if any(p.match(line) for p in [_ALT_PAREN_BOTH, _ALT_PAREN_RIGHT, _ALT_DOT]):
                in_alt_section = True
            if in_alt_section:
                continue
            m = _ASSOC_ITEM.match(line)
            if m:
                items.append({"label": m.group(1), "text": m.group(2).strip()})
        return items

    def _infer_question_type(
        self, lines: list[str], alternatives: dict[str, str]
    ) -> QuestionType:
        if sum(1 for l in lines if _ROMAN_ITEM.match(l)) >= 2:
            return QuestionType.roman_numeral

        if sum(1 for l in lines if _TRUE_FALSE_ITEM.match(l)) >= 2:
            return QuestionType.true_false

        assoc_items = self._extract_association_items(lines)
        if len(assoc_items) >= 2:
            alt_text = " ".join(alternatives.values())
            if re.search(r"\b\d\b.*\b\d\b", alt_text):
                return QuestionType.association

        return QuestionType.simple

    def _compute_confidence(
        self,
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
        if len(alternatives) < 5:
            score -= 0.3
        if len(enunciado.strip()) < 20:
            score -= 0.2
        return max(0.0, min(1.0, score))

    def _build_enunciado(self, lines: list[str], alternatives: dict[str, str]) -> str:
        stem_lines: list[str] = []
        for line in lines:
            if self._question_number(line) is not None:
                continue
            is_alt = False
            for pattern in [_ALT_PAREN_BOTH, _ALT_PAREN_RIGHT, _ALT_DOT]:
                m = pattern.match(line)
                if m and m.group(1).upper() in "ABCDE":
                    is_alt = True
                    break
            if not is_alt:
                stem_lines.append(line)
        return "\n".join(stem_lines).strip()

    def _inject_vignettes(self, questions: list[ParsedQuestion]) -> list[ParsedQuestion]:
        result = list(questions)
        for i in range(1, len(result)):
            q = result[i]
            prev = result[i - 1]
            if len(q.enunciado.strip()) < 50:
                paragraphs = [p.strip() for p in prev.enunciado.split("\n\n") if p.strip()]
                if len(paragraphs) >= 2:
                    vignette = paragraphs[-1]
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

    def _parse_block(
        self,
        number: int,
        raw_block: str,
        section_map: dict[int, str],
        result: ParseResult,
    ) -> None:
        lines = raw_block.splitlines()
        alternatives, _pattern_name, ambiguous = self._extract_alternatives(lines)
        question_type = self._infer_question_type(lines, alternatives)

        items: list[dict] | None = None
        if question_type == QuestionType.roman_numeral:
            items = self._extract_roman_items(lines) or None
        elif question_type == QuestionType.true_false:
            items = self._extract_true_false_items(lines) or None
        elif question_type == QuestionType.association:
            items = self._extract_association_items(lines) or None

        enunciado = self._build_enunciado(lines, alternatives)

        raw_section = section_map.get(number, "unknown")
        try:
            section = SectionType(raw_section)
        except ValueError:
            section = SectionType.unknown

        confidence = self._compute_confidence(
            alternatives=alternatives,
            ambiguous_pattern=ambiguous,
            question_type=question_type,
            enunciado=enunciado,
        )

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
