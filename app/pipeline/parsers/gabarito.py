"""Gabarito (answer key) parser for ENARE multi-caderno PDFs."""

import re
from dataclasses import dataclass, field

# Matches caderno section headers in two known ENARE formats:
#   "Residência Multi/Uniprofissional - Enfermagem - 1 - Turno Tarde"  (final gabarito)
#   "ENFERMAGEM (ENFERMT01) - PROVA TIPO  1"                           (gabarito preliminar)
_CADERNO_SPLIT_RE = re.compile(
    r"(Resid[êe]ncia\s+(?:Multi/Uniprofissional|Uniprofissional|Multi)\b[^\n]+"
    r"|[A-ZÁÉÍÓÚÂÊÔÀÃÕÜÇ][A-ZÁÉÍÓÚÂÊÔÀÃÕÜÇ/ ]+\([A-Z0-9]+\)\s*-\s*PROVA\s+TIPO\s+\d+"
    r"|\*{0,2}[A-ZÁÉÍÓÚÂÊÔÀÃÕÜÇ][A-ZÁÉÍÓÚÂÊÔÀÃÕÜÇa-záéíóúâêôàãõüç/ ]+\s*-\s*PROVA\s+\d+\*{0,2})",
    re.IGNORECASE,
)

_FOOTNOTE_RE = re.compile(r"\*\s*Quest[aã]o\s+anulada", re.IGNORECASE)

# Two question numbers on a single pdfminer line, e.g. "99  100" or "79 80"
_TWO_NUMS_RE = re.compile(r"^(\d{1,3})\s+(\d{1,3})$")


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

        # Two-column page recovery — backward: some gabarito PDFs place Q1-5 for
        # the SECOND caderno on a page inside the body TWO sections earlier.
        # Use "first-occurrence wins" so we get the first caderno's data.
        if 1 not in answers and i >= 3:
            two_back_text = _first_q1_5_block_after_pagebreak(parts[i - 3])
            if two_back_text:
                extra = _extract_answers_first_wins(two_back_text)
                for q, a in extra.items():
                    if q not in answers:
                        answers[q] = a

        # Two-column page recovery — forward: Q16-20 groups for the FIRST caderno
        # on a two-column page appear as the FIRST occurrence in the body TWO
        # sections ahead.  Only trigger when answers are suspiciously few.
        if 16 not in answers and len(answers) < 80 and i + 3 < len(parts):
            fwd_text = _q16_section(parts[i + 3])
            if fwd_text:
                extra = _extract_answers_first_wins(fwd_text)
                for q, a in extra.items():
                    if q not in answers:
                        answers[q] = a

        if answers:
            cadernos.append(CadernoAnswers(name=name, answers=answers))

    return cadernos


def _q16_section(text: str) -> str:
    """Return the portion of *text* starting from the first Q16 token onwards.

    Two-column pages write Q16-20 data for BOTH cadernos in the body of the
    second caderno, with the first caderno's data appearing before the second's.
    Slicing to Q16+ and using first-occurrence wins gives the first caderno's
    Q16-20 answers without contamination from Q6-15 of the second caderno.
    """
    typed = _tokenise(text)
    start = next((i for i, t in enumerate(typed) if t == ("num", 16)), None)
    if start is None:
        return ""
    # Re-serialise just the tokens from Q16 onwards into a synthetic text that
    # _extract_answers_first_wins can process (it re-tokenises internally).
    # The simplest way is to return the original text sliced to the position
    # of Q16 — find the char offset of the first "16" occurrence.
    idx16 = text.find("\n16\n")
    if idx16 == -1:
        idx16 = text.find("\n16 \n")
    if idx16 == -1:
        return ""
    return text[idx16:]


def _first_q1_5_block_after_pagebreak(text: str) -> str:
    """Return all text after the last page-break in *text*.

    Two-column gabarito pages write the pre-header Q1-5 data for BOTH cadernos
    after the page-break of the preceding standalone caderno.  The second
    caderno's data appears first (left column), so "first-occurrence wins"
    extraction gives the right answers.
    """
    if "\x0c" not in text:
        return ""
    return text.rsplit("\x0c", 1)[1]


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

    typed = _tokenise(text)
    return _pairs_to_map(_group_pairs(typed))


def _extract_answers_first_wins(text: str) -> dict[int, str | None]:
    """Like _extract_answers but keeps the FIRST occurrence of each question.

    Used when recovering Q1-5 data from a two-column page section where the
    target caderno's data appears before the neighbouring caderno's data.
    """
    table_result = _parse_table(text)
    if table_result:
        return table_result  # tables are already positional

    typed = _tokenise(text)
    pairs = _group_pairs(typed)
    result: dict[int, str | None] = {}
    for num, ans in pairs:
        if 1 <= num <= 100 and num not in result:
            result[num] = None if ans == "*" else ans
    return result


def _tokenise(text: str) -> list[tuple[str, int | str]]:
    """Convert pdfminer text into a stream of ("num", N) / ("ans", A) tokens.

    Handles the common case where two consecutive question numbers appear on
    the same line (e.g. "99  100" or "79  80") by splitting them into two
    separate num-tokens.  Without this, the trailing answer tokens for those
    questions become orphaned and corrupt the answer-group immediately before
    them (the N-nums vs M-answers mismatch causes the whole group to be
    dropped).
    """
    typed: list[tuple[str, int | str]] = []
    for raw_line in text.splitlines():
        tok = re.sub(r"\*{2,}", "", raw_line).strip()

        # Two numbers on one line — split into two num-tokens
        m2 = _TWO_NUMS_RE.match(tok)
        if m2:
            n1, n2 = int(m2.group(1)), int(m2.group(2))
            if 1 <= n1 <= 100:
                typed.append(("num", n1))
            if 1 <= n2 <= 100:
                typed.append(("num", n2))
            continue

        if tok.isdigit() and 1 <= int(tok) <= 100:
            typed.append(("num", int(tok)))
        elif tok in ("A", "B", "C", "D", "E", "*"):
            typed.append(("ans", tok))

    return typed


def _group_pairs(typed: list[tuple[str, int | str]]) -> list[tuple[int, str]]:
    """Walk the token stream grouping consecutive num-runs with answer-runs."""
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
    return pairs


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
