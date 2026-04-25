# DocAssist — Build Plan

DocAssist is a Quote & Invoice Generator for small Austrian businesses (under 10 employees, no ERP). It is the first module of a larger "AI administrative assistant for Austrian SMEs" platform. This document covers the V1 scope, architecture, user experience, all 8 portfolio criteria, and the phased build plan.

---

## Problem Statement (README draft)

**The problem:** An Austrian SME owner — carpenter, IT consultant, business advisor — spends 30–60 unbillable minutes on every quote and invoice. They open a Word template, type line items, calculate totals, check formatting, add bank details. If they make an error, their client cannot reclaim VAT. They do this for every job, every week, for years.

**The user:** Austrian sole trader or micro-business owner (under 10 employees), no accounting software. Operates in one of three pilot industries: Reinigung / Gebäudereinigung, Handwerk / Tischler, Unternehmensberatung.

**What solved looks like:**
- Quote generated from a plain-text or spoken job description in under 30 seconds
- §11 UStG compliance: 100% of required fields present on every generated invoice
- Quote-to-invoice conversion: zero additional data entry from the owner
- Owner's time per document: from 30–60 minutes to under 5 minutes of review and send
- Cost to serve: under €0.10 per document generated

---

## V1 Scope Decisions

All locked before building begins.

**Industries in scope:** Reinigung / Gebäudereinigung, Handwerk / Tischler, Unternehmensberatung. These three cover the three dominant invoice shapes in Austrian SME land: hourly-rate service, labour-plus-materials trade, day-rate consulting.

**Input:** Plain text typed or spoken by the owner. Examples:
- `"Rechnung an Müller GmbH, 16 Stunden à 65 Euro, Material 840 Euro"` → rates in input, extracted directly
- `"Rechnung an Müller GmbH, Küchenmontage 2 Tage"` → no rates, system falls back to configured defaults

**Rate handling:**
- Rates mentioned in input → extracted and used directly
- Rates not mentioned → look up `default_rates` in `config/business_profile.yaml`
- No default configured for that service type → clarification branch fires, system asks before generating
- Human review gate before sending catches anything that slipped through
- No raw placeholder text (`[RATE]`) in the final document

**Client data:**
- `data/clients.json` with 8–10 synthetic Austrian companies for the demo
- Agent calls a `lookup_client` tool with fuzzy matching ("Müller" → "Müller Bau GmbH")
- No match → clarification branch asks for client details, optionally saves new record
- Post-MVP: CSV import, then full client management UI

**VAT:** 20% default. Owner overrides in input ("inkl. 10% MwSt"). Rules engine reads the rate from extraction or applies 20%. LLM never decides the rate.

**Language:** German default. EN/DE configurable via `business_profile.yaml`. One Jinja2 template per language per document type.

**Business profile setup (V1):** Owner fills in `config/business_profile.yaml` once. No UI until post-MVP.

```yaml
name: "Tischlerei Muster GmbH"
address: "Hauptstraße 12, 4020 Linz"
uid: "ATU12345678"
logo_path: "assets/logo.png"
bank_iban: "AT12 3456 7890 1234 5678"
bank_bic: "RZOOAT2L"
brand_color: "#2C3E50"
language: "de"
default_rates:
  labor_hourly: 85
  labor_daily: 550
  material_markup_pct: 15
```

**PDF generation:** Jinja2 HTML templates → WeasyPrint. Agent generates data (Pydantic `DocumentModel`); template handles all presentation (A4, margins, font, logo, brand colour).

**Deployment:** Google Cloud Run, `europe-west3` (Frankfurt). Correct region for Austrian users, scales to zero, generous free tier.

**SevDesk integration:** Strictly post-MVP. Output schema not designed for it in V1.

---

## What the Owner Experiences

The landing page is minimal — API-first product, UI is scaffolding:
- One-sentence problem statement
- A single text input (or microphone button) for the job description
- Business profile status indicator ("Ihr Profil ist eingerichtet")
- Link to `/docs` for developers

**The flow:**

1. Owner types or speaks: `"Angebot für Weber IT Solutions, Website-Relaunch, 40 Stunden Entwicklung à 90 Euro, 8 Stunden Projektmanagement à 110 Euro"`
2. Page shows: "Erstelle Angebot… (~15s)"
3. Draft quote appears in browser for review — line items, totals, client address, payment terms
4. Owner clicks **Freigeben** → system converts to a §11 UStG-compliant Rechnung
5. PDF download available; optionally emailed to client

**What they achieve:** a 30–60 minute task compressed to 5 minutes of reading and one click.

---

## How the Agentic System Produces This

```
Job description (text / voice input)
              │
              ▼
    ┌───────────────────────┐
    │  Scope Extraction     │  Haiku → structured scope (Pydantic)
    │  Agent                │  client ref, services, rates, confidence
    └──────────┬────────────┘
               │
        low confidence? ──yes──► Clarification Request
               │                  (ask owner; await answer; loop back)
               │ no
               ▼
    ┌───────────────────────┐
    │  Client Lookup Tool   │  Fuzzy match clients.json
    │                       │  No match → ask for details
    └──────────┬────────────┘
               │
               ▼
    ┌───────────────────────┐
    │  Rate Resolution      │  Input rates → use directly
    │                       │  Missing → business_profile defaults
    │                       │  No default → clarification branch
    └──────────┬────────────┘
               │
               ▼
    ┌───────────────────────┐
    │  Quote Generation     │  Sonnet → line items, totals,
    │  Agent                │  payment terms, validity period
    └──────────┬────────────┘
               │
               ▼
    ┌───────────────────────┐
    │  Human Review Gate    │  LangGraph interrupt()
    │  (Angebot shown)      │  Owner: Freigeben / Ändern / Ablehnen
    └──────────┬────────────┘
               │
        rejected? ──yes──► back to Quote Generation with feedback
               │ approved
               ▼
    ┌───────────────────────┐
    │  Invoice Generator    │  Deterministic: map accepted quote
    │  (§11 UStG)           │  fields → Rechnung Pydantic model
    └──────────┬────────────┘
               │
               ▼
    ┌───────────────────────┐
    │  Compliance Check     │  Deterministic rules engine
    │  (rules engine)       │  All 11 §11 UStG fields valid?
    └──────────┬────────────┘
               │
        fails? ──yes──► Compliance Correction Agent (Haiku)
               │         fills gaps with context from state
               │          └── loop back (max 2 retries)
               │ passes
               ▼
    ┌───────────────────────┐
    │  PDF Renderer         │  Jinja2 + WeasyPrint → A4 PDF
    │                       │  Injects business_profile + DocumentModel
    └──────────┬────────────┘
               │
               ▼
    ┌───────────────────────┐
    │  Response             │  PDF download + SQLite log
    │  + Observability      │  LangSmith trace per request
    └───────────────────────┘
```

**Why this shape fits LangGraph:**
- The **human review gate** (`interrupt()`) is the strongest justification. The graph suspends, persists full state, and resumes when the owner clicks Freigeben — hours or days later. A linear pipeline cannot do this.
- The **clarification branches** (low confidence, missing rate, unknown client) are genuine conditional routing, not decoration.
- The **compliance correction loop** retries with context on failure, capped at 2 iterations, and the retry count is logged per request.

---

## The §11 UStG Required Fields (Compliance Check Ground Truth)

The rules engine checks all 11 fields deterministically:

1. Supplier name and address
2. Recipient name and address
3. Supplier UID number (ATU...)
4. Recipient UID number (required if invoice > €10,000)
5. Sequential invoice number (fortlaufende Nummer)
6. Invoice date
7. Date of delivery or service period (Leistungsdatum / Leistungszeitraum)
8. Quantity and description of goods/services
9. Net amount broken down by VAT rate
10. Applicable VAT rate(s)
11. VAT amount (or reverse charge reference if applicable)

---

## The 8 Portfolio Criteria

### Criterion 1 — Specified problem, not a tool demo
README opens with: Austrian SME owner, 30–60 min per document, no checker. Solved means: <30s generation, 100% compliance, <€0.10/doc, owner time <5 min. Numbers are targets at start; replaced with achieved numbers at completion.

**Pass.**

### Criterion 2 — Eval suite predates optimisation

Commit sequence: `feat: eval harness` lands before `feat: extraction agent`. Three layers:

**Deterministic (built before any LLM exists):**
- §11 UStG field presence: 11 fields, either present or not. Ground truth is Austrian law.
- VAT arithmetic: line items × rate = correct total. Exact answer.
- UID number format: `ATU` + 8 digits. Regex.
- Client extraction accuracy: did the agent identify the correct client ref from the input?
- Adversarial: vague input → clarification triggered, not hallucinated line items

**LLM-as-judge (manual effort: one afternoon):**
- Write 25–30 job description → gold quote pairs (one set per industry)
- Pairwise comparison rubric: which generated quote better matches the gold?
- Judge with Sonnet comparing against a Haiku output — documents model tradeoff

**Eval harness:** pytest-based, `scripts/eval.py` produces a JSON scorecard committed to repo. Stubs run before any real implementation.

**Pass.**

### Criterion 3 — Model and component choices with documented tradeoffs

Three comparisons to run and document with own numbers:

1. **Sonnet vs Haiku for scope extraction** — F1 on line-item relevance, especially for multi-service jobs and German-language input
2. **Structured output (Pydantic tool-use) vs free-form then parse** — reliability of valid JSON output; does Haiku hold up?
3. **Local (Qwen2.5-32B) vs Anthropic API** — quality delta on extraction; used during development phase, documented in `docs/tradeoffs.md`

Decision documented: "We use Sonnet at quote generation because Haiku's line-item relevance F1 dropped from 0.89 to 0.74 on multi-service Handwerk jobs. We use Haiku for compliance correction because judged quality was indistinguishable."

**Pass.**

### Criterion 4 — Prompts as versioned code

```
prompts/
  scope_extraction/
    v1.md
    v2.md        ← after eval shows v1 misses German trade terminology
  quote_generation/
    v1.md
  compliance_correction/
    v1.md
  CHANGELOG.md   ← version → eval score → what changed
```

`CHANGELOG.md` is the criterion 4 artifact: diffs with metric deltas next to them.

**Pass.**

### Criterion 5 — Structured observability

SQLite schema — one row per node per request:

```
request_id | timestamp | input_hash | node | model | input_tokens
output_tokens | latency_ms | cost_eur | industry_type | compliance_passed | error
```

LangSmith for traces. Canned queries answerable in under a minute:
- p95 latency on scope extraction by industry
- Compliance pass rate over time (do newer prompts fail less?)
- Which input types trigger the clarification branch most?
- Haiku vs Sonnet cost per document

**Pass.**

### Criterion 6 — Documented failure modes and mitigations

| Failure | How it manifests | Mitigation |
|---|---|---|
| **Hallucinated line items** | LLM invents services not in the job description | Extraction-first schema constrains what can be generated; human review gate |
| **Wrong VAT rate** | Job type miscategorised | Explicit VAT rate lookup — LLM never decides the rate; rules engine applies it |
| **Silent compliance failure** | Invoice generated with a missing §11 field | Deterministic post-generation compliance check; correction loop; non-compliant document never returned |
| **Vague input hallucination** | "Mach eine Rechnung" → LLM invents full scope | Confidence score on extraction; below threshold → clarification request, not generation |
| **Price fabrication** | LLM invents rates when none configured | Clarification branch fires before generation if no rate source available |
| **Client not found** | "Rechnung an Müller" matches nothing | Clarification branch asks for full client details; auto-saves for next time |

Documented in `docs/failure_modes.md`.

**Pass.**

### Criterion 7 — Deployed beyond localhost

```
POST /quote          job description → draft Angebot (async, returns request-ID)
POST /invoice/{id}   owner-approved quote → §11 UStG Rechnung + PDF
GET  /status/{id}    poll for async result
GET  /healthz        uptime check
```

API key auth middleware, `slowapi` rate limiting, error handling. Deployed on Google Cloud Run `europe-west3`. Real URL in README with working `curl` example.

**Pass.**

### Criterion 8 — Cost and latency profiled

Estimated per-document cost breakdown (to be replaced with measured numbers):

| Node | Model | Est. tokens | Est. cost |
|---|---|---|---|
| Scope extraction | Haiku | ~400 in / 200 out | ~€0.0002 |
| Quote generation | Sonnet | ~600 in / 400 out | ~€0.008 |
| Compliance correction (if triggered) | Haiku | ~300 in / 150 out | ~€0.0001 |
| **Total** | | | **~€0.008–0.01** |

Run same 30-document test set through all-Haiku variant. Document the quality/cost tradeoff. At 20 quotes/month, owner's cost: under €0.20/month to serve. Document in `docs/cost_latency.md`.

**Pass.**

---

## Build Phases

Total budget: ~100–150 hours across 8–12 weeks.

**Key rule:** `feat: eval harness` commits before `feat: extraction agent`. The git history is part of the portfolio artifact.

---

### Phase 0 — Problem Definition & README Skeleton

**What it's doing:** Writing the README problem statement before any code. The north star.

**What it requires:**
- Write: problem statement, target user, "what solved looks like" with numbers
- Write: 25–30 job description → gold output pairs (one afternoon), one set per industry. This is the LLM-as-judge training set and it must exist before prompt tuning begins.

**Deliverable:** `README.md` Section 1 + `evals/gold/` folder with annotated job description pairs. Commit #1.

**Time:** 4–6 hours.

---

### Phase 1 — Data Foundation

**What it's doing:** Securing all test data before any LLM call exists.

**What it requires:**
- Build a minimal Jinja2 invoice template (simplified Phase 8 precursor) — enough to render a valid A4 PDF with all 11 §11 UStG fields. Used only for gold set generation; the polished template comes in Phase 8.
- Write `scripts/generate_gold_set.py`: parameterised by industry, company, line items, amounts. Produces 15 PDFs (5 per industry) in `evals/gold/` with a matching `ground_truth.json` per file. Also generates adversarial variants: missing UID, malformed VAT, long service descriptions.
- Write `scripts/generate_test_invoices.py`: larger synthetic set (50+) of known-good and known-bad invoices for eval harness. Covers edge cases and all three industries.
- Populate `data/clients.json` with 8–10 synthetic Austrian companies (varied industries, realistic addresses, UID numbers in correct format).
- 30-minute spike: confirm WeasyPrint renders A4 correctly on Windows; pick PDF parsing library (`pdfplumber` recommended) and confirm it can round-trip the generated PDFs.

Note: no real client invoices used. Synthetic gold set from our own template gives exact ground truth and avoids all privacy concerns.

**Deliverable:** `data/` and `evals/` directories fully populated. Synthetic generator runnable via `python scripts/generate_test_invoices.py`.

**Time:** 6–10 hours. - /res

---

### Phase 2 — Eval Harness

**What it's doing:** Writing the ruler before anything to measure.

**What it requires:**
- Build pytest-based harness with stub functions returning dummy output
- Implement deterministic metric functions: §11 field presence checker, VAT arithmetic validator, UID format validator, client extraction accuracy scorer
- Implement LLM-as-judge harness: pairwise comparison of generated vs gold quote (placeholder rubric prompt, to be refined in Phase 5)
- Build `scripts/eval.py` → JSON scorecard committed to repo

**Deliverable:** `pytest -q evals/` returns a numerical scorecard in <10 seconds against stubs. Scorecard format locked.

**Time:** 6–10 hours.

---

### Phase 3 — Scope Extraction Agent

**What it's doing:** First real LLM component. First defensible model choice with own numbers.

**Local dev LLM setup:**
- Model: `gemma-4-26b-a4b-it-mlx` running on LM Studio
- Endpoint: `http://192.168.1.181:1234/v1` (fixed IP, OpenAI-compatible REST API)
- Client: `openai` Python SDK with `base_url` pointed at local server, `api_key="local"`
- Production swap: change `base_url` and `api_key` to Anthropic SDK — no other code changes needed

**What it requires:**
- Design Pydantic schema: `ScopeModel` (client_ref, services[], rates{}, vat_rate, confidence_score, language)
- Build extraction against local Gemma first, then swap to Claude Sonnet/Haiku for production. Record F1 on client extraction and line-item relevance against gold pairs for both — this is the local vs API tradeoff data for Criterion 3.
- Prompts: `/prompts/scope_extraction/v1.md`. Run eval after each version. Record delta in `CHANGELOG.md`.
- Implement confidence scoring: self-reported by model (confidence: "high"/"low") with deterministic override — force "low" if `services` is empty or `client_ref` is missing, regardless of model's self-report.
- Implement clarification branch: returns a structured question to the owner rather than proceeding.

**Deliverable:** `extractor` module returning validated `ScopeModel`. Evals show ≥85% F1 on line-item relevance across all three industries. `docs/tradeoffs.md` with local Gemma vs Claude Sonnet/Haiku numbers.

**Time:** 15–20 hours.

---

### Phase 4 — Rate Resolution & Client Lookup

**What it's doing:** The deterministic tooling layer. Proves you know when not to use an LLM.

**What it requires:**
- Build `lookup_client(ref: str)` tool: fuzzy string match against `clients.json`. Returns full client record or `None`.
- Build `resolve_rates(scope: ScopeModel, profile: BusinessProfile)` function: input rates → use; missing → profile defaults; no default → return list of unresolved items for clarification branch.
- Write unit tests for both. No LLM involved.

**Deliverable:** Two tested utility functions. LangGraph tools registered. The clarification branch now has concrete items to ask about rather than generic uncertainty.

**Time:** 4–6 hours.

---

### Phase 5 — Quote Generation Agent

**What it's doing:** Core generation step. Where LLM-as-judge eval does its real work.

**What it requires:**
- Build quote generation with Sonnet: takes resolved `ScopeModel` + `ClientRecord` + `BusinessProfile` → returns `QuoteModel` (Pydantic: line_items[], subtotal, vat_amount, total, payment_terms, validity_days).
- Prompts: `/prompts/quote_generation/v1.md`. Iterate against gold pairs. Track version → eval score in `CHANGELOG.md`.
- Refine LLM-as-judge rubric from Phase 2 placeholder into a working pairwise comparison prompt. Validate: sample 20 judge outputs, score manually, confirm >80% agreement.

**Deliverable:** `quote_generator` module returning validated `QuoteModel`. LLM-as-judge eval shows ≥80% pairwise preference for generated vs baseline across all three industries.

**Time:** 15–20 hours.

---

### Phase 6 — Compliance Rules Engine

**What it's doing:** Deterministic §11 UStG compliance layer.

**What it requires:**
- Before coding: download the ebInterface 6.1 XSD from [erechnung.gv.at](https://www.erechnung.gv.at/erb/en_GB/tec_formats_ebinterface) and cross-check all 11 §11 UStG fields against it. The XSD is the authoritative machine-readable definition of required Austrian invoice fields — use it to confirm the field list is complete and the naming is correct before writing any rules.
- Build `compliance_check(invoice: InvoiceModel) → ComplianceResult` function: checks all 11 §11 UStG fields, returns list of failures with field names.
- Build `InvoiceModel` Pydantic schema: all 11 required fields, plus bank details, document number.
- Write `docs/ruleset_rationale.md`: why each field is required, source (§11 UStG subsection), and corresponding ebInterface element name.
- Full pytest coverage. Evals: synthetic invoice generator's known-bad invoices give direct precision/recall numbers.

**Deliverable:** `compliance_engine.py` with 100% precision on the synthetic test set. Eval numbers committed. Field list verified against ebInterface XSD.

**Time:** 5–8 hours.

---

### Phase 7 — LangGraph Assembly

**What it's doing:** Composing all prior components into the formal graph with state management.

**What it requires:**
- Design `DocAssistState` TypedDict: `raw_input`, `scope`, `client`, `resolved_rates`, `quote`, `approval_status`, `invoice`, `compliance_result`, `pdf_bytes`, `clarifications_needed`, `per_node_metadata` (timing, cost, model per node).
- Build all nodes wired to prior phase functions.
- Implement `interrupt()` at human review gate — this is the key LangGraph-specific pattern. State persists across the suspend/resume boundary.
- Implement compliance correction loop (Haiku fills gaps, routes back to compliance check, capped at 2).
- Build model-per-node config in `config/models.yaml`: swap Haiku/Sonnet per node without code changes.

**Deliverable:** `graph.invoke({"raw_input": "..."})` produces complete state including PDF bytes. End-to-end eval suite runs against real graph, not stubs.

**Time:** 10–15 hours.

---

### Phase 8 — PDF Renderer

**What it's doing:** Presentation layer — entirely separate from the agent logic.

**What it requires:**
- HTML/CSS templates for Angebot and Rechnung in `templates/de/` and `templates/en/`.
- A4 CSS `@page` rules, margins, brand colour injection, logo as base64-embedded.
- `render_pdf(model: DocumentModel, profile: BusinessProfile, lang: str) → bytes` function using WeasyPrint.
- Confirm WeasyPrint renders correctly on Windows (known minor quirks with font loading).

**Deliverable:** `renderer.py` producing a clean A4 PDF from any valid `DocumentModel`. Template is a static file — owner can customise it without touching agent code.

**Time:** 5–8 hours.

---

### Phase 9 — Observability

**What it's doing:** Criterion 5. The thing that makes this credible as a production system.

**What it requires:**
- SQLite schema and write path: one row per node per request, linked by `request_id`.
- LangSmith integration (free tier).
- `scripts/query.py` with 5–6 canned queries (p95 latency per node, compliance pass rate, cost by industry, clarification branch trigger rate).
- Verify queries answer ops questions in under a minute.

**Deliverable:** SQLite accumulating real data. `python scripts/query.py --report` prints a formatted summary.

**Time:** 8–12 hours.

---

### Phase 10 — FastAPI + Cloud Run Deployment

**What it's doing:** Criterion 7. Getting a real URL.

**What it requires:**
- Async FastAPI endpoints: `POST /quote`, `POST /invoice/{id}`, `GET /status/{id}`, `GET /healthz`.
- `BackgroundTasks` + SQLite for job state (no Celery/Redis in V1).
- API key middleware, `slowapi` rate limiting.
- `Dockerfile` — slim Python image, WeasyPrint system dependencies, non-root user.
- Deploy to Cloud Run `europe-west3`: `gcloud run deploy` or GitHub Actions workflow.
- Confirm real URL works with `curl` example.

**Deliverable:** Live endpoint. 20-line quickstart in README with working `curl` command.

**Time:** 10–15 hours.

---

### Phase 11 — Cost & Latency Profiling

**What it's doing:** Criterion 8. The analysis that almost nobody produces.

**What it requires:**
- Run 30-document test set through: (1) primary Sonnet/Haiku mix, (2) all-Haiku variant.
- Record per-node latency distributions, per-node cost, total per document, p50/p95.
- Compute: cost at 20 docs/month (realistic), cost at 100 docs/month (growth scenario).
- Write `docs/cost_latency.md` with tables and the tradeoff story.

**Deliverable:** Markdown doc with real tables. Defended model choices backed by own numbers.

**Time:** 4–6 hours.

---

### Phase 12 — Failure Modes Doc & README Polish

**What it's doing:** Criteria 1 and 6. Narrative work.

**What it requires:**
- Write `docs/failure_modes.md`: all 6 failure modes, how the system catches each, what happens when it doesn't.
- Write README final pass: problem, user, achieved numbers (not targets), quickstart, architecture diagram, links to cost/latency doc, failure modes doc, eval report, live API endpoint.
- Record 2–3 minute Loom walkthrough: type a job description, see the quote, click Freigeben, receive the PDF.

**Deliverable:** A repo that reads like a production system's documentation.

**Time:** 3–5 hours.

---

## Post-MVP (Not in V1 Plan)

Noted for later, not blocking:

- **Frontend UI v2:** User registration, company profile settings page, logo upload from browser.
- **Client management:** CSV import of client list, client edit/delete UI.
- **SevDesk integration:** Structured JSON output compatible with SevDesk import.
- **E-invoicing:** ZUGFeRD / Factur-X XML output for Austrian e-invoicing mandate compliance.
- **Payment reminders:** Natural extension — generated invoices feed a reminder pipeline (see Option 1 from project scoping).
- **Additional industries:** Expand beyond the three pilot industries.
- **Voice input:** Microphone button → Whisper transcription → existing text pipeline.
- **Multi-language:** Additional languages beyond DE/EN.

---

## Local Development Stack

- **Orchestration:** LangGraph
- **Language:** Python
- **LLM (local dev):** LM Studio with `Qwen2.5-32B-Instruct` (Q4_K_M, ~20GB VRAM). Alternative for faster prompt iteration: `Phi-4` (14B, ~9GB).
- **LLM (production):** Anthropic Claude — Sonnet for extraction/generation, Haiku for compliance correction and formatting
- **Observability:** LangSmith (tracing) + SQLite (persistent request logs)
- **Deployment:** FastAPI + Google Cloud Run (`europe-west3`)
- **PDF generation:** Jinja2 + WeasyPrint
- **Evals:** Custom pytest suite + LLM-as-judge
- **Prompt management:** Versioned files in `/prompts/`, tracked in git
- **Vector store:** Not required for V1 (no RAG needed; rates and clients are structured lookups)