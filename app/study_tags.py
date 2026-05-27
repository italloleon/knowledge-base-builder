"""Derive note/tag vocabulary from edital JSON (knowledge_areas, titles)."""

from __future__ import annotations

from typing import Any

_TAG_CAP = 600


def flatten_tags_from_editais(rows: list[tuple[Any, ...]]) -> list[str]:
    """Rows: list of (edition_name, numero_edital, knowledge_areas JSON)."""
    tags: list[str] = []
    seen: set[str] = set()

    def add(raw: str | None) -> bool:
        """Returns True if tag cap reached."""
        if raw is None:
            return False
        s = str(raw).strip()
        if len(s) < 2 or len(s) > 240:
            return False
        key = s.casefold()
        if key in seen:
            return False
        seen.add(key)
        tags.append(s)
        return len(tags) >= _TAG_CAP

    for edition_name, numero_edital, knowledge_areas in rows:
        if add(edition_name):
            return sorted(tags, key=str.casefold)
        if add(str(numero_edital) if numero_edital is not None else None):
            return sorted(tags, key=str.casefold)

        ka = knowledge_areas or []
        if not isinstance(ka, list):
            continue
        for block in ka:
            if not isinstance(block, dict):
                continue
            if add(block.get("profissao")):
                return sorted(tags, key=str.casefold)
            for sec in ("gerais", "especificos"):
                for area in block.get(sec) or []:
                    if not isinstance(area, dict):
                        continue
                    if add(area.get("area")):
                        return sorted(tags, key=str.casefold)
                    for t in area.get("topicos") or []:
                        if isinstance(t, str) and add(t):
                            return sorted(tags, key=str.casefold)

    return sorted(tags, key=str.casefold)
