# Knowledge Base Builder
## Service Specification — Parsing Only

> **Active Personas**:
> - `agents/data-engineer.md`
> - `agents/devops-automator.md`
>
> Read both agent files before writing any code. The personas define
> the standards and philosophy. This document defines the requirements.

---

## Goal

Accept ENARE exam PDF files, parse them into structured questions using
deterministic rules only, and store the results in PostgreSQL.
No LLM involved at this stage.

This service is the foundation. Parsing quality will be validated manually
before any AI layer is introduced.

---

## Ingestion

The service must accept PDF files in two ways:

- **Direct upload** — multipart file upload from a client
- **Remote URL** — a publicly accessible URL pointing to a PDF

Both modes must produce the same output. The difference in source is an
implementation detail of the ingestion layer, invisible to the rest of the pipeline.

Only PDF files are accepted. Size limit is configurable via environment variable.

---

## Pipeline Stages

The pipeline has three sequential stages. Each stage has a single responsibility
and must not bleed into the next.

### Stage 1 — Extraction
Convert the PDF to Markdown using Docling.

The output must be a clean text representation of the document with structure
preserved. If the output is empty or suspiciously short, the stage must fail
loudly — never pass bad input downstream.

### Stage 2 — Preprocessing
Clean the Markdown before parsing.

ENARE PDFs contain repeating page headers and footers on every page that are
noise for the parser. These must be stripped. Section headings
("Conhecimentos Gerais", "Conhecimentos Específicos") must be identified and
preserved as metadata — the parser needs to know which section each question
belongs to.

### Stage 3 — Parsing
Convert clean Markdown into structured question records using deterministic rules.

This is the most important stage. The agent must design the parsing logic
based on the structural patterns observed in ENARE/FGV PDFs:

**What the agent knows about the source format:**
- Questions are numbered sequentially (1–100)
- Each question number appears on its own line
- Each question has exactly 5 alternatives labeled A through E
- Alternatives follow consistent punctuation patterns: `(A)`, `A)`, or `A.`
- Some questions have Roman numeral items (I, II, III) before the alternatives
- Some questions have True/False items marked with empty parentheses `( )`
- Some questions have a numbered association list matched to the alternatives
- Some questions share a clinical vignette — a case description that precedes
  two or more numbered questions and belongs to all of them

**What the parser must produce per question:**
- Question number
- Section (Conhecimentos Gerais or Conhecimentos Específicos)
- Question type — inferred from structure, not hardcoded
- Full question stem (enunciado), including any shared vignette
- Items list if present (I/II/III or V/F statements)
- All five alternatives (A through E)
- The original unparsed text block — kept for debugging
- A confidence score — lower when the parser had to make assumptions

**What the parser must never do:**
- Silently drop a question block
- Infer or guess the correct answer (gabarito)
- Truncate the enunciado

**Parse errors:**
Any block the parser cannot confidently structure must be saved as a parse
error with the original text and the reason for failure. These are not
discarded — they are persisted for manual review.

---

## Data Model

The agent decides the implementation, but the following entities must exist:

**Exam** — one record per processed PDF. Must include the source filename and
a hash of the file content. The hash is used for idempotency: submitting the
same PDF twice must not create duplicate records.

**Question** — one record per parsed question. Must include the confidence
score and the original raw block. The `gabarito` field exists but is nullable —
it will be populated separately from the official answer key, not inferred by
the parser.

**Job** — tracks the lifecycle of a processing request. Must expose counts:
total questions found, successfully parsed, and parse errors. Status must
reflect partial completion when some blocks failed.

**Parse Error** — one record per block that failed parsing. Must include the
original text and the failure reason. This table is the primary tool for
evaluating parser quality.

---

## API

The service exposes a REST API designed to be consumed by a future frontend.

**Ingestion endpoints:**
- Submit a PDF file for processing
- Submit a remote PDF URL for processing
- Both return a job ID immediately — processing is asynchronous

**Status endpoint:**
- Poll a job by ID
- Returns current status and all counts

**Exam endpoints:**
- List all processed exams
- List questions for an exam, filterable by section, type, and confidence score
- Retrieve a single question including its raw block
- List parse errors for an exam

The confidence score filter is important: it allows the future frontend to
surface questions that need human review before the LLM layer is introduced.

**Health endpoints:**
- Liveness probe — is the service running
- Readiness probe — are dependencies (database, queue) reachable

---

## Authentication

Authentication is not enforced at this stage but must be structurally prepared.

The mechanism is an API key passed in a request header. A middleware component
must exist that enforces the key when enabled and passes all requests through
when disabled. Toggling is done via environment variable — no code change required.

Health and readiness endpoints are always public regardless of auth state.

---

## Infrastructure

The entire service must deploy with `docker compose up --build`.
No manual steps are acceptable after that command.

**Required services:**
- PostgreSQL — primary data store
- Redis — job queue
- API — FastAPI application
- Worker — async task processor (ARQ)
- Migrate — runs database migrations before API and worker start

**Dependency ordering must be strict:**
- Migrate runs after PostgreSQL is healthy, then exits
- API and Worker start only after migrate completes successfully
- No service starts before its dependencies are healthy

**Every service must have a health check.**

**All configuration via environment variables.** No hardcoded values.
A `.env.example` must document every required variable.

**Persistent data must survive restarts.** PostgreSQL data, Redis data,
and uploaded files must be stored in named volumes.

**Images must be minimal and run as non-root.**

---

## What Comes Next

Once parse errors are near zero and confidence scores are consistently high
across multiple PDFs, the LLM layer will be introduced as an optional
enrichment pass.

At that point the LLM will receive the already-parsed `raw_block` of
low-confidence questions — not raw Markdown — which is a much smaller
and cleaner input. The parser output is the LLM's input, not a replacement for it.