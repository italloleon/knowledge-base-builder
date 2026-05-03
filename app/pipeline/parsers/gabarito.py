"""Gabarito (answer key) parser for ENARE multi-caderno PDFs."""

import re
from dataclasses import dataclass, field

# Matches caderno section headers, e.g.:
#   "Residência Multi/Uniprofissional - Enfermagem - 1 - Turno Tarde"
_CADERNO_SPLIT_RE = re.compile(
    r"(Resid[êe]ncia\s+(?:Multi/Uniprofissional|Uniprofissional|Multi)\b[^\n]+)",
    re.IGNORECASE,
)

_FOOTNOTE_RE = re.compile(r"\*\s*Quest[aã]o\s+anulada", re.IGNORECASE)


@dataclass
class CadernoAnswers:
    name: str
    answers: dict[int, str | None] = field(default_factory=dict)


def parse_gabarito(text: str) -> list[CadernoAnswers]:
    """Parse gabarito text into per-caderno answer maps.

    In ENARE PDFs, questions 1-4 appear immediately BEFORE each caderno header
    and questions 5-100 appear after it — both in a grouped layout where N
    question numbers are followed by N answers.  Also handles the alternating
    one-number / one-answer format and Docling markdown tables.

    Answer value is None for annulled questions (marked with *).
    """
    parts = _CADERNO_SPLIT_RE.split(text)
    cadernos: list[CadernoAnswers] = []

    # parts = [pre0, name1, body1, name2, body2, ...]
    for i in range(1, len(parts) - 1, 2):
        name = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""

        # Questions 1-4 live in the text segment BEFORE this header:
        #   - For the first caderno that is parts[0] (the preamble).
        #   - For later cadernos it is the tail of the previous body, i.e. the
        #     text that follows "* Questão anulada" in parts[i-1].
        preceding = parts[i - 1]
        footnote_split = _FOOTNOTE_RE.split(preceding, maxsplit=1)
        q1_4_text = footnote_split[-1]  # tail after footnote (or whole preamble)

        # Truncate the main body at the footnote so stray tokens don't pollute it
        body_main = _FOOTNOTE_RE.split(body, maxsplit=1)[0]

        combined = q1_4_text + "\n" + body_main
        answers = _extract_answers(combined)
        if answers:
            cadernos.append(CadernoAnswers(name=name, answers=answers))

    return cadernos


def _extract_answers(text: str) -> dict[int, str | None]:
    """Extract answer pairs from text in any of the three supported formats:

    1. Grouped (raw pdfminer / most ENARE PDFs):
       N numbers (each on its own line / separated by whitespace) followed by
       N answers in the same grouping pattern.
    2. Alternating (one number then one answer, pdfminer):
       ``1\\nD\\n2\\nC``
    3. Markdown tables (Docling grid output):
       ``| 1 | 2 | 3 |``
       ``| A | B | C |``
    """
    # Try markdown tables first — if present the text likely came from Docling
    table_result = _parse_table(text)
    if table_result:
        return table_result

    # Tokenise line-by-line: pdfminer emits each value on its own line
    # (separated by \n\n), so "Página 4 de 22" is one line and must be
    # ignored — only lines whose entire content is a single number or letter
    # are valid tokens.
    typed: list[tuple[str, int | str]] = []
    for raw_line in text.splitlines():
        tok = re.sub(r"\*{2,}", "", raw_line).strip()
        if tok.isdigit() and 1 <= int(tok) <= 100:
            typed.append(("num", int(tok)))
        elif tok in ("A", "B", "C", "D", "E", "*"):
            typed.append(("ans", tok))

    # Walk the token stream grouping consecutive runs of numbers with the
    # immediately following run of answers (handles both alternating 1:1 and
    # grouped N:N layouts).
    pairs: list[tuple[int, str]] = []
    idx = 0
    while idx < len(typed):
        if typed[idx][0] == "num":
            nums: list[int] = []
            while idx < len(typed) and typed[idx][0] == "num":
                nums.append(typed[idx][1])  # type: ignore[arg-type]
                idx += 1
            answers: list[str] = []
            while idx < len(typed) and typed[idx][0] == "ans":
                answers.append(typed[idx][1])  # type: ignore[arg-type]
                idx += 1
            if len(nums) == len(answers):
                pairs.extend(zip(nums, answers))
            # If counts differ the group is malformed — skip rather than mis-pair
        else:
            idx += 1

    return _pairs_to_map(pairs)


def _pairs_to_map(pairs) -> dict[int, str | None]:
    result: dict[int, str | None] = {}
    for num, ans in pairs:
        if 1 <= num <= 100:
            result[num] = None if ans == "*" else ans
    return result


def _parse_table(text: str) -> dict[int, str | None]:
    """Parse markdown tables (Docling table output) into answer pairs."""
    pairs: list[tuple[int, str]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line.startswith("|"):
            i += 1
            continue
        cells = [re.sub(r"\*+", "", c).strip() for c in line.split("|")[1:-1]]
        non_empty = [c for c in cells if c]
        if non_empty and all(c.isdigit() for c in non_empty):
            nums = [int(c) for c in non_empty]
            j = i + 1
            if j < len(lines) and re.match(r"^\s*\|[-| ]+\|\s*$", lines[j]):
                j += 1
            if j < len(lines) and lines[j].strip().startswith("|"):
                ans_cells = [
                    re.sub(r"\*+", "", c).strip()
                    for c in lines[j].split("|")[1:-1]
                ]
                ans_vals = [c for c in ans_cells if c]
                for num, ans in zip(nums, ans_vals):
                    if 1 <= num <= 100 and (ans in "ABCDE" or ans == "*"):
                        pairs.append((num, ans))
            i = j + 1
        else:
            i += 1
    return _pairs_to_map(pairs)
