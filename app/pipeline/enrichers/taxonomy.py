"""Helpers for taxonomy-grounded question enrichment."""

from __future__ import annotations

import difflib
import json
import logging
import re
import unicodedata
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"\s+", " ", value.strip().lower())
    return value


def _compact_area_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    area_map: "OrderedDict[str, OrderedDict[str, None]]" = OrderedDict()
    for node in nodes:
        area = (node.get("area") or "").strip()
        if not area:
            continue
        topics_bucket = area_map.setdefault(area, OrderedDict())
        for topic in node.get("topicos") or []:
            if isinstance(topic, str):
                t = topic.strip()
                if t:
                    topics_bucket.setdefault(t, None)
    return [{"area": area, "topics": list(topics.keys())} for area, topics in area_map.items()]


def _iter_bucket_nodes(knowledge_areas: list[Any], bucket: str) -> list[dict[str, Any]]:
    """Yield nodes for one bucket (gerais/especificos)."""
    nodes: list[dict[str, Any]] = []
    for entry in knowledge_areas:
        if not isinstance(entry, dict):
            continue
        # Legacy shape: treat as specific competence by default.
        if isinstance(entry.get("area"), str):
            if bucket == "especificos":
                nodes.append(entry)
            continue
        groups = entry.get(bucket)
        if not isinstance(groups, list):
            continue
        for area_node in groups:
            if isinstance(area_node, dict) and isinstance(area_node.get("area"), str):
                nodes.append(area_node)
    return nodes


def build_taxonomy_context(
    knowledge_areas: list[Any] | None,
    edital_id: str | None = None,
) -> dict[str, Any] | None:
    """Create compact taxonomy payload for prompt grounding.

    The context keeps both edital competency groups:
    - competencias_gerais
    - competencias_especificas
    """
    if not knowledge_areas:
        return None

    gerais = _compact_area_nodes(_iter_bucket_nodes(knowledge_areas, "gerais"))
    especificos = _compact_area_nodes(_iter_bucket_nodes(knowledge_areas, "especificos"))
    compact_areas = _compact_area_nodes(
        _iter_bucket_nodes(knowledge_areas, "gerais")
        + _iter_bucket_nodes(knowledge_areas, "especificos")
    )
    if not compact_areas:
        return None

    return {
        "edital_id": edital_id,
        "competencias_gerais": gerais,
        "competencias_especificas": especificos,
        "areas": compact_areas,
    }


def taxonomy_for_prompt(taxonomy_context: dict[str, Any], max_chars: int = 4500) -> str:
    """Render compact JSON for prompt, capped to avoid oversized payloads."""
    rendered = json.dumps(
        {
            "competencias_gerais": taxonomy_context.get("competencias_gerais", []),
            "competencias_especificas": taxonomy_context.get("competencias_especificas", []),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(rendered) <= max_chars:
        return rendered

    def _trim(areas: list[dict[str, Any]], max_local_chars: int) -> list[dict[str, Any]]:
        trimmed: list[dict[str, Any]] = []
        for area_obj in areas:
            area = area_obj.get("area")
            topics = area_obj.get("topics") or []
            if not isinstance(area, str):
                continue
            kept_topics: list[str] = []
            for topic in topics:
                if not isinstance(topic, str):
                    continue
                candidate = json.dumps(
                    {"areas": trimmed + [{"area": area, "topics": kept_topics + [topic]}]},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                if len(candidate) > max_local_chars:
                    break
                kept_topics.append(topic)
            candidate_obj = {"area": area, "topics": kept_topics}
            candidate = json.dumps(
                {"areas": trimmed + [candidate_obj]},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if len(candidate) > max_local_chars:
                break
            trimmed.append(candidate_obj)
            if max_local_chars - len(candidate) <= 32:
                break
        return trimmed

    half = max_chars // 2
    trimmed_payload = {
        "competencias_gerais": _trim(taxonomy_context.get("competencias_gerais", []), half),
        "competencias_especificas": _trim(taxonomy_context.get("competencias_especificas", []), half),
    }
    compact = json.dumps(trimmed_payload, ensure_ascii=False, separators=(",", ":"))
    if len(compact) <= max_chars:
        return compact

    # Last-resort fallback keeps old compact "areas" representation.
    rendered = json.dumps(taxonomy_context.get("areas", []), ensure_ascii=False, separators=(",", ":"))
    if len(rendered) <= max_chars:
        return rendered

    trimmed: list[dict[str, Any]] = []
    for area_obj in taxonomy_context.get("areas", []):
        area = area_obj.get("area")
        topics = area_obj.get("topics") or []
        if not isinstance(area, str):
            continue
        # Keep area name and as many topics as fit.
        kept_topics: list[str] = []
        for topic in topics:
            if not isinstance(topic, str):
                continue
            candidate = json.dumps(
                trimmed + [{"area": area, "topics": kept_topics + [topic]}],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if len(candidate) > max_chars:
                break
            kept_topics.append(topic)
        candidate_obj = {"area": area, "topics": kept_topics}
        candidate = json.dumps(trimmed + [candidate_obj], ensure_ascii=False, separators=(",", ":"))
        if len(candidate) > max_chars:
            break
        trimmed.append(candidate_obj)
        if max_chars - len(candidate) <= 32:
            break

    return json.dumps(trimmed, ensure_ascii=False, separators=(",", ":"))


def _best_match(value: str, options: list[str]) -> str | None:
    """Return canonical string from options, or None (used only where mode is not needed)."""
    m, _mode = _match_to_canonical(value, options)
    return m


def _match_to_canonical(
    value: str, options: list[str]
) -> tuple[str | None, str | None]:
    """Map free text to one of options; returns (canonical, mode).

    mode: exact | fuzzy | None (unmapped).
    """
    if not value or not options:
        return None, None
    norm_value = _norm(value)
    if not norm_value:
        return None, None
    norm_to_original = {_norm(opt): opt for opt in options}
    if norm_value in norm_to_original:
        return norm_to_original[norm_value], "exact"
    for norm_opt, original in norm_to_original.items():
        if norm_value in norm_opt or norm_opt in norm_value:
            return original, "fuzzy"
    close = difflib.get_close_matches(norm_value, list(norm_to_original.keys()), n=1, cutoff=0.58)
    if close:
        return norm_to_original[close[0]], "fuzzy"
    return None, None


def _resolve_area_topic_pair(
    raw_area: str,
    raw_topic: str,
    area_labels: list[str],
    by_area_topics: dict[str, list[str]],
    *,
    all_topics: list[str],
    label: str,
) -> tuple[str | None, str | None, str, str]:
    """Resolve area/topic to canonical labels; never pick an arbitrary default.

    Returns (matched_area, matched_topic, area_match_mode, topic_match_mode).
    area_match_mode: exact | fuzzy | inferred | unmapped
    topic_match_mode: exact | fuzzy | unmapped
    """
    ra = (raw_area or "").strip()
    rt = (raw_topic or "").strip()

    m_area, area_mode = _match_to_canonical(ra, area_labels)

    if m_area is None and rt:
        owners: list[str] = []
        for area, topics in by_area_topics.items():
            t_canon, _tmode = _match_to_canonical(rt, topics)
            if t_canon is not None:
                owners.append(area)
        if len(owners) == 1:
            m_area = owners[0]
            area_mode = "inferred"
        elif len(owners) > 1:
            logger.info(
                "taxonomy [%s]: ambiguous topic %r matches multiple areas: %s",
                label,
                rt,
                owners,
            )

    if m_area is None:
        if ra or rt:
            logger.info(
                "taxonomy [%s]: unmapped area (raw_area=%r raw_topic=%r)",
                label,
                ra,
                rt,
            )
        return None, None, "unmapped", "unmapped"

    m_topic, topic_mode = _match_to_canonical(rt, by_area_topics.get(m_area, []))
    if m_topic is None and rt:
        m_topic, topic_mode = _match_to_canonical(rt, all_topics)

    if m_topic is None:
        topic_mode = "unmapped"
        if rt:
            logger.info(
                "taxonomy [%s]: unmapped topic under area %r (raw_topic=%r)",
                label,
                m_area,
                rt,
            )

    tmode = topic_mode or "unmapped"
    amode = area_mode or "unmapped"
    return m_area, m_topic, amode, tmode


def enforce_taxonomy(
    enrichment: dict[str, Any] | None,
    taxonomy_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Map area/topic and competência fields to official labels when possible.

    Does not default to the first area/topic on failure. Sets taxonomy_match metadata
    and taxonomy_grounded only when both area and topic resolve to list values.
    """
    if enrichment is None or taxonomy_context is None:
        return enrichment

    areas = taxonomy_context.get("areas") or []
    area_labels = [a.get("area") for a in areas if isinstance(a.get("area"), str)]
    if not area_labels:
        return enrichment

    by_area_topics = {
        a["area"]: [t for t in (a.get("topics") or []) if isinstance(t, str)]
        for a in areas
        if isinstance(a, dict) and isinstance(a.get("area"), str)
    }
    all_topics = [t for topics in by_area_topics.values() for t in topics]

    raw_area = str(enrichment.get("area") or "").strip()
    raw_topic = str(enrichment.get("topic") or "").strip()

    matched_area, matched_topic, area_mm, topic_mm = _resolve_area_topic_pair(
        raw_area,
        raw_topic,
        area_labels,
        by_area_topics,
        all_topics=all_topics,
        label="question",
    )

    enrichment["area"] = matched_area
    enrichment["topic"] = matched_topic

    details_main = {
        "area_match": area_mm,
        "topic_match": topic_mm,
    }

    def _match_competencia(
        area_field: str,
        topic_field: str,
        bucket_key: str,
    ) -> dict[str, str]:
        bucket = taxonomy_context.get(bucket_key) or []
        labels = [a.get("area") for a in bucket if isinstance(a.get("area"), str)]
        by_topics = {
            a["area"]: [t for t in (a.get("topics") or []) if isinstance(t, str)]
            for a in bucket
            if isinstance(a, dict) and isinstance(a.get("area"), str)
        }
        if not labels:
            return {"area_match": "unmapped", "topic_match": "unmapped"}

        raw_a = str(enrichment.get(area_field) or "").strip()
        raw_t = str(enrichment.get(topic_field) or "").strip()
        all_b = [t for topics in by_topics.values() for t in topics]

        m_area, m_topic, amm, tmm = _resolve_area_topic_pair(
            raw_a,
            raw_t,
            labels,
            by_topics,
            all_topics=all_b,
            label=bucket_key,
        )

        enrichment[area_field] = m_area
        enrichment[topic_field] = m_topic
        return {"area_match": amm, "topic_match": tmm}

    cg = _match_competencia(
        area_field="competencia_geral_area",
        topic_field="competencia_geral_topico",
        bucket_key="competencias_gerais",
    )
    ce = _match_competencia(
        area_field="competencia_especifica_area",
        topic_field="competencia_especifica_topico",
        bucket_key="competencias_especificas",
    )

    enrichment["taxonomy_match"] = {
        "question": details_main,
        "competencia_geral": cg,
        "competencia_especifica": ce,
    }

    grounded = (
        matched_area is not None
        and matched_topic is not None
        and area_mm in ("exact", "fuzzy", "inferred")
        and topic_mm in ("exact", "fuzzy")
    )
    enrichment["taxonomy_grounded"] = grounded
    enrichment["taxonomy_needs_review"] = not grounded

    return enrichment
