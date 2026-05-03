"""Edital (exam notice) parser — regex Tier 1 + LLM Tier 2."""

from __future__ import annotations

import re

from app.pipeline.base import DocumentParser, EditalData, ParseResult, PreprocessResult

# ---------------------------------------------------------------------------
# Tier 1 — deterministic regex patterns
# ---------------------------------------------------------------------------

_EDITAL_NUM_RE = re.compile(r"Edital\s+n[º°o\.]\s*([\d]+/\d{4})", re.IGNORECASE)
_EDITION_RE = re.compile(r"(Enare\s+\d{4}(?:/\d{4})?)", re.IGNORECASE)
_ANO_RE = re.compile(r"Enare\s+(\d{4})", re.IGNORECASE)
_ORGANIZADORA_RE = re.compile(r"Banca\s+Examinadora\s+da\s+(\w+)", re.IGNORECASE)
_GESTORA_RE = re.compile(r"\b(Ebserh)\b")
_MODALIDADE_RE = re.compile(
    r"(Residência\s+Multiprofissional\s+e\s+em\s+Área\s+Profissional\s+da\s+Saúde(?:\s+\(Uniprofissional\))?)",
    re.IGNORECASE,
)
_QUESTOES_GERAIS_RE = re.compile(
    r"(\d+)\s+questões?\s+objetivas?\s+relacionadas?\s+a\s+Competências?\s+Gerais?",
    re.IGNORECASE,
)
_QUESTOES_ESP_RE = re.compile(
    r"(\d+)\s+questões?\s+objetivas?\s+relacionadas?\s+a\s+Competências?\s+Específicas?",
    re.IGNORECASE,
)
_PERC_MIN_RE = re.compile(r"percentual\s+mínimo\s+de\s+(\d+)%", re.IGNORECASE)
_BOLSA_RE = re.compile(
    r"bolsa.residência\s+mensal.{0,180}R[$]\s*([\d.]+,\d{2})",
    re.IGNORECASE | re.DOTALL,
)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}")
_TEL_RE = re.compile(r"\b(0800[\s\d-]{7,12})\b")
_URL_RE = re.compile(r"https://[\w./%-]+")
_DATA_INICIO_RE = re.compile(
    r"início\s+do\s+ano\s+letivo.{0,200}?março\s+de\s+(\d{4})",
    re.IGNORECASE | re.DOTALL,
)

# Anexo III knowledge-area block patterns
_ANEXO_III_RE = re.compile(
    r"ANEXO\s+III\s*[–-]\s*PROVA\s+OBJETIVA[:\s]*(CONHECIMENTOS[^\n]*)",
    re.IGNORECASE,
)
_PROFESSION_HEADER_RE = re.compile(
    r"^([A-ZÁÉÍÓÚÂÊÔÃÕÇÀÜ /]+)\s*[–-]\s*COMPETÊNCIAS?\s+ESPECÍFICAS?",
    re.MULTILINE | re.IGNORECASE,
)
_GERAIS_HEADER_RE = re.compile(
    r"COMPETÊNCIAS?\s+GERAIS?\b",
    re.IGNORECASE,
)
_AREA_RE = re.compile(
    r"^(?:ÁREA|EIXO)\s+\d+\s*[–:.]\s*(.+)$",
    re.MULTILINE | re.IGNORECASE,
)
_TOPIC_RE = re.compile(
    r"^\s*(?:\d+[\d.]*\.?\s+|[-•]\s+)(.+)$",
    re.MULTILINE,
)


def _first_match(pattern: re.Pattern, text: str, group: int = 1) -> str | None:
    m = pattern.search(text)
    return m.group(group).strip() if m else None


def _parse_bolsa(text: str) -> float | None:
    m = _BOLSA_RE.search(text)
    if not m:
        return None
    raw = m.group(1).replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_areas_block(block: str) -> list[dict]:
    """Parse a block of text containing numbered areas and their topics."""
    areas: list[dict] = []
    current_area: str | None = None
    current_topics: list[str] = []

    for line in block.splitlines():
        area_m = _AREA_RE.match(line)
        if area_m:
            if current_area is not None:
                areas.append({"area": current_area, "topicos": current_topics})
            current_area = area_m.group(1).strip()
            current_topics = []
            continue
        topic_m = _TOPIC_RE.match(line)
        if topic_m and current_area is not None:
            topic = topic_m.group(1).strip()
            if topic:
                current_topics.append(topic)

    if current_area is not None:
        areas.append({"area": current_area, "topicos": current_topics})

    return areas


def _extract_knowledge_areas(text: str) -> list[dict]:
    """Extract knowledge areas from Anexo III when present in the document."""
    if not _ANEXO_III_RE.search(text):
        return []

    # Isolate Anexo III block — everything from the header to the next ANEXO
    anx_start = _ANEXO_III_RE.search(text).start()
    next_anx = re.search(r"\bANEXO\s+IV\b", text[anx_start + 10:], re.IGNORECASE)
    anx_block = text[anx_start: anx_start + 10 + next_anx.start()] if next_anx else text[anx_start:]

    knowledge_areas: list[dict] = []

    # Extract competências gerais (shared across all professions)
    gerais_areas: list[dict] = []
    gerais_m = _GERAIS_HEADER_RE.search(anx_block)
    if gerais_m:
        # Find where specific professions start
        first_prof = _PROFESSION_HEADER_RE.search(anx_block)
        gerais_end = first_prof.start() if first_prof else len(anx_block)
        gerais_block = anx_block[gerais_m.end():gerais_end]
        gerais_areas = _parse_areas_block(gerais_block)

    # Extract per-profession specific competências
    for m in _PROFESSION_HEADER_RE.finditer(anx_block):
        profession = m.group(1).strip()
        next_m = _PROFESSION_HEADER_RE.search(anx_block, m.end())
        block_end = next_m.start() if next_m else len(anx_block)
        spec_block = anx_block[m.end():block_end]
        especificos = _parse_areas_block(spec_block)

        knowledge_areas.append(
            {
                "profissao": profession,
                "gerais": gerais_areas,
                "especificos": especificos,
            }
        )

    # If no per-profession sections but gerais exist, emit a single entry
    if not knowledge_areas and gerais_areas:
        knowledge_areas.append(
            {"profissao": "COMUNS", "gerais": gerais_areas, "especificos": []}
        )

    return knowledge_areas


# ---------------------------------------------------------------------------
# EditalParser
# ---------------------------------------------------------------------------


class EditalParser(DocumentParser):
    """Parser for ENARE edital (notice/announcement) PDFs.

    Stage 2 (preprocess): strips repetitive page headers, normalises whitespace.
    Stage 3 (parse): returns an empty ParseResult (editals have no questions).

    Call extract_edital(markdown) for the structured EditalData result.
    """

    # Running page header emitted on every PDF page by Docling
    _PAGE_HEADER_RE = re.compile(
        r"Edital\s+n[º°o\.]\s*[\d/]+\s*[–-]\s*Enare\s+\d{4}[^\n]*\n",
        re.IGNORECASE,
    )
    # Numeric page-number line (standalone "  42  " etc.)
    _PAGE_NUM_RE = re.compile(r"^\s*\d{1,3}\s*$", re.MULTILINE)

    def preprocess(self, markdown: str) -> PreprocessResult:
        text = self._PAGE_HEADER_RE.sub("", markdown)
        text = self._PAGE_NUM_RE.sub("", text)
        # Collapse runs of blank lines to a single blank
        text = re.sub(r"\n{3,}", "\n\n", text)
        return PreprocessResult(clean_text=text.strip())

    def parse(self, preprocess_result: PreprocessResult) -> ParseResult:
        # Editals contain no questions — callers use extract_edital() instead.
        return ParseResult()

    # ------------------------------------------------------------------
    # Public extraction API
    # ------------------------------------------------------------------

    def extract_edital(self, markdown: str) -> EditalData:
        """Run full extraction pipeline and return structured EditalData."""
        pre = self.preprocess(markdown)
        text = pre.clean_text

        numero_edital = _first_match(_EDITAL_NUM_RE, text)
        edition_name_raw = _first_match(_EDITION_RE, text)
        edition_name = edition_name_raw.strip() if edition_name_raw else None

        ano: int | None = None
        m_ano = _ANO_RE.search(text)
        if m_ano:
            try:
                ano = int(m_ano.group(1))
            except ValueError:
                pass

        organizadora = _first_match(_ORGANIZADORA_RE, text)
        instituicao_gestora = _first_match(_GESTORA_RE, text)
        modalidade = _first_match(_MODALIDADE_RE, text)

        tq_gerais: int | None = None
        m_g = _QUESTOES_GERAIS_RE.search(text)
        if m_g:
            try:
                tq_gerais = int(m_g.group(1))
            except ValueError:
                pass

        tq_esp: int | None = None
        m_e = _QUESTOES_ESP_RE.search(text)
        if m_e:
            try:
                tq_esp = int(m_e.group(1))
            except ValueError:
                pass

        perc_min: float | None = None
        m_p = _PERC_MIN_RE.search(text)
        if m_p:
            try:
                perc_min = float(m_p.group(1))
            except ValueError:
                pass

        bolsa = _parse_bolsa(text)

        data_inicio: str | None = None
        m_di = _DATA_INICIO_RE.search(text)
        if m_di:
            data_inicio = f"1º dia útil de março de {m_di.group(1)}"

        email_m = _EMAIL_RE.search(text)
        contato_email = email_m.group(0) if email_m else None

        tel_m = _TEL_RE.search(text)
        contato_telefone = tel_m.group(1).strip() if tel_m else None

        urls = _URL_RE.findall(text)
        # Prefer the canonical Enare URL
        url_enare = next(
            (u for u in urls if "enare.ebserh.gov.br" in u), urls[0] if urls else None
        )

        knowledge_areas = _extract_knowledge_areas(text)

        return EditalData(
            numero_edital=numero_edital,
            ano=ano,
            edition_name=edition_name,
            organizadora=organizadora,
            instituicao_gestora=instituicao_gestora,
            modalidade=modalidade,
            total_questoes_gerais=tq_gerais,
            total_questoes_especificas=tq_esp,
            percentual_minimo_aprovacao=perc_min,
            bolsa_mensal=bolsa,
            data_inicio_programas=data_inicio,
            contato_email=contato_email,
            contato_telefone=contato_telefone,
            url_enare=url_enare,
            knowledge_areas=knowledge_areas,
        )
