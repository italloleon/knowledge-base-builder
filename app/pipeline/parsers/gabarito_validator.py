"""Cross-validation between two gabarito parser results.

Phase 1 — observability only.  The primary result is always returned by
the caller; this module only produces warning strings that the caller can
attach to each ``GabaritoCaderno``.
"""

import difflib
import logging
import re
import unicodedata

from app.pipeline.parsers.gabarito import CadernoAnswers

logger = logging.getLogger(__name__)


def _normalize_name(name: str) -> str:
    """Lowercase, strip accents, collapse whitespace, strip punctuation."""
    # Strip accents via NFKD decomposition
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    lower = ascii_str.lower()
    # Strip punctuation
    no_punct = re.sub(r"[^\w\s]", "", lower)
    # Collapse whitespace
    return re.sub(r"\s+", " ", no_punct).strip()


def _match_cadernos(
    primary: list[CadernoAnswers],
    fallback: list[CadernoAnswers],
) -> list[tuple[CadernoAnswers, CadernoAnswers]]:
    """Return matched (primary_caderno, fallback_caderno) pairs.

    Matching strategy:
    1. Exact normalized name match.
    2. difflib.SequenceMatcher ratio >= 0.85 as fallback.

    Unmatched cadernos are logged and skipped.
    """
    norm_fallback: dict[str, CadernoAnswers] = {
        _normalize_name(c.name): c for c in fallback
    }

    pairs: list[tuple[CadernoAnswers, CadernoAnswers]] = []
    for p in primary:
        p_norm = _normalize_name(p.name)

        # Step 1: exact normalized match
        if p_norm in norm_fallback:
            pairs.append((p, norm_fallback[p_norm]))
            continue

        # Step 2: fuzzy match
        best_key: str | None = None
        best_ratio = 0.0
        for f_norm in norm_fallback:
            ratio = difflib.SequenceMatcher(None, p_norm, f_norm).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_key = f_norm

        if best_key is not None and best_ratio >= 0.85:
            pairs.append((p, norm_fallback[best_key]))
        else:
            logger.warning(
                "cross_validate: could not match primary caderno %r to any fallback caderno "
                "(best ratio=%.2f) — skipping",
                p.name,
                best_ratio,
            )

    return pairs


def cross_validate(
    primary: list[CadernoAnswers],
    fallback: list[CadernoAnswers],
) -> dict[str, list[str]]:
    """Compare two parser results and return per-caderno warning strings.

    Args:
        primary: Cadernos produced by the primary (LLM) parser.
        fallback: Cadernos produced by the fallback (regex) parser.

    Returns:
        A dict mapping each primary caderno name to a list of warning strings.
        Categories:
        - Discrepancy: both parsers have the question but disagree on the answer.
        - Coverage gap: one parser has the question but the other does not.

        Returns ``{}`` on any unexpected exception.
    """
    try:
        result: dict[str, list[str]] = {}

        matched_pairs = _match_cadernos(primary, fallback)

        for p_caderno, f_caderno in matched_pairs:
            warnings: list[str] = []

            p_answers: dict[int, str | None] = p_caderno.answers
            f_answers: dict[int, str | None] = f_caderno.answers

            all_keys = set(p_answers.keys()) | set(f_answers.keys())

            for q in sorted(all_keys):
                in_primary = q in p_answers
                in_fallback = q in f_answers

                if in_primary and in_fallback:
                    if p_answers[q] != f_answers[q]:
                        warnings.append(
                            f"Q{q}: primary={p_answers[q]} fallback={f_answers[q]}"
                        )
                elif in_primary and not in_fallback:
                    warnings.append(f"Q{q}: missing in fallback")
                else:
                    warnings.append(f"Q{q}: missing in primary")

            if warnings:
                result[p_caderno.name] = warnings

        return result

    except Exception as exc:  # noqa: BLE001
        logger.warning("cross_validate: unexpected error — %s", exc, exc_info=True)
        return {}
