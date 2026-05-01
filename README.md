# DocAssist

> Quote and invoice generation for small Austrian businesses — from a plain-text job description to a §11 UStG-compliant PDF in under 30 seconds.

---

## The Problem

An Austrian SME owner without ERP or accounting software — carpenter, cleaner, business advisor — spends ca. 15-30 unbillable minutes on every quote and invoice. They open a Word template, type line items, calculate totals, add bank details, check the layout. They do this for every job, every week.

The work is not skilled. It is just slow.

**DocAssist** takes a plain-text or spoken job description and produces a structured quote for review, then a compliant invoice on approval — with zero reformatting and zero data re-entry.

---

## Target User

Austrian sole trader or micro-business owner, under 10 employees, no accounting software. Pilot industries: Handwerk / Tischler, Reinigung, Unternehmensberatung.

---

## What "Solved" Looks Like

| Metric | Target | Achieved |
|---|---|---|
| Quote generation time | < 30 seconds | ~5s end-to-end (profiled) |
| §11 UStG field compliance | 100% on every invoice | 100% — non-compliant document never returned |
| Quote-to-invoice data entry | Zero additional input | Zero — deterministic mapping |
| Owner time per document | < 5 minutes (review + send) | < 5 minutes — review draft, click Freigeben |
| Cost to serve | < €0.10 per document | €0.007 avg (profiled, all-Haiku) |

---

## How It Works

A LangGraph agent processes the job description through a sequence of nodes:

1. **Scope extraction** — parses client, services, rates, and confidence from free text (EN or DE)
2. **Client lookup** — fuzzy-matches against the business's client list
3. **Rate resolution** — uses input rates or falls back to configured defaults; asks if neither exists
4. **Quote generation** — produces structured line items, totals, and payment terms
5. **Human review gate** — owner reviews and approves the draft in the chat interface
6. **Invoice generation** — deterministically maps approved quote to §11 UStG fields
7. **Compliance check** — rules engine verifies all 11 required fields; correction loop if any fail
8. **PDF render** — Jinja2 + WeasyPrint produces a branded A4 document

The human review gate uses LangGraph's `interrupt()` — the graph suspends, persists state, and resumes when the owner approves.

---

## Quickstart

Live demo: `https://docassist-86540080152.europe-west3.run.app` (Cloud Run, `europe-west3`). Swagger UI at [`/docs`](https://docassist-86540080152.europe-west3.run.app/docs).

**Simplest path — browser UI:** Open [https://docassist-86540080152.europe-west3.run.app](https://docassist-86540080152.europe-west3.run.app) in a browser, paste your API key into the key field, type a German job description, and click **Angebot erstellen**. Review the quote draft, then click **Freigeben** — the §11 UStG PDF downloads automatically.

**Try it against the live API** (replace `$API_KEY` with the issued key):

```bash
# 1. Submit a job description — returns a request_id
curl -s -X POST https://docassist-86540080152.europe-west3.run.app/quote \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"raw_input":"Tischlerarbeit für Maria Huber: Eichentisch, 2 Tage à 8h, EUR 75/h."}'

# 2. Poll status until "awaiting_approval" — surfaces the structured quote draft
curl -s https://docassist-86540080152.europe-west3.run.app/status/$REQUEST_ID \
  -H "X-API-Key: $API_KEY"

# 3. Approve to render the §11 UStG-compliant PDF
curl -s -X POST https://docassist-86540080152.europe-west3.run.app/invoice/$REQUEST_ID \
  -H "X-API-Key: $API_KEY" -o invoice.pdf
```

**Run locally:**

```bash
git clone https://github.com/leonp84/document-agent.git
cd document-agent
pip install -r requirements.txt
cp .env.example .env  # fill in API keys + DOCASSIST_API_KEY

cp config/business_profile.example.yaml config/business_profile.yaml
# edit business_profile.yaml with your details

uvicorn main:app --reload --port 8000
```

Local API at `http://localhost:8000` — Swagger at `/docs`, health probe at `/health`.

---

## Eval Report

Pytest suite: **26/28 passing** (100% of active tests), 2 xfailed stub placeholders. See [`evals/scorecard.json`](evals/scorecard.json).

| Metric | Result |
|---|---|
| Extraction client accuracy | 100% (24 gold pairs, all models) |
| Extraction service F1 | 0.94 (Haiku, production model) |
| Compliance precision | 100% on synthetic test set (known-good and known-bad invoices) |
| Compliance pass rate (profiling) | 100% — correction loop + clarification ensures every document is compliant before delivery |

Model comparison (Haiku vs Sonnet vs local): [`docs/tradeoffs.md`](docs/tradeoffs.md).

---

## Cost & Latency

Profiled against 10 documents (all-Haiku and Sonnet-for-quote variants):

| | All-Haiku | Sonnet quote generation |
|---|---|---|
| Avg cost/document | €0.007 | €0.009 |
| p50 latency | 4.1s | 4.6s |
| p95 latency | 8.2s | 6.6s |
| Cost at 20 docs/month | €0.14 | €0.18 |

Sonnet costs 30% more for quote generation with no observable quality difference on structured output. Full per-node breakdown: [`docs/cost_latency.md`](docs/cost_latency.md).

---

## Compliance Standard

Invoice field requirements are derived from §11 UStG and cross-referenced against [ebInterface](https://www.erechnung.gv.at/erb/en_GB/tec_formats_ebinterface), the Austrian Chamber of Commerce's XML standard for e-invoices. ebInterface XML export is planned post-MVP.

---

## Failure Modes

See [`docs/failure_modes.md`](docs/failure_modes.md) for documented failure modes and mitigations (hallucinated line items, wrong VAT rate, silent compliance failure, vague input, price fabrication, unknown client).

---

## Docs

| Document | What it covers |
|---|---|
| [`docs/tradeoffs.md`](docs/tradeoffs.md) | Extraction model comparison — Haiku vs Sonnet vs local models (F1, latency, cost) |
| [`docs/cost_latency.md`](docs/cost_latency.md) | Per-node latency and cost profile, Haiku vs Sonnet quote generation comparison, scale projections |
| [`docs/failure_modes.md`](docs/failure_modes.md) | Six documented failure modes, mitigations, and known limitations |
| [`docs/ruleset_rationale.md`](docs/ruleset_rationale.md) | §11 UStG field-by-field rationale, cross-referenced against ebInterface XSD |
| [`evals/scorecard.json`](evals/scorecard.json) | Latest test suite results |

---

## Stack

| Component | Choice |
|---|---|
| Orchestration | LangGraph |
| LLM | Anthropic Claude (Sonnet / Haiku) |
| API | FastAPI + uvicorn |
| PDF | Jinja2 + WeasyPrint |
| Observability | LangSmith + SQLite |
| Deployment | Google Cloud Run (europe-west3) |

---

## Project Structure

```
docassist/
├── app/                   # FastAPI application
├── agent/                 # LangGraph graph and nodes
├── prompts/               # Versioned prompt files
├── config/                # business_profile.yaml, models.yaml
├── data/                  # clients.json
├── templates/             # Jinja2 invoice/quote templates (de/, en/)
├── evals/                 # Eval harness and gold set
│   └── gold/              # Annotated gold job description pairs
├── scripts/               # eval.py, generate_gold_set.py
└── docs/                  # tradeoffs.md, cost_latency.md, failure_modes.md
```
