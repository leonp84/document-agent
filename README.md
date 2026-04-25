# DocAssist

> Quote and invoice generation for small Austrian businesses — from a plain-text job description to a §11 UStG-compliant PDF in under 30 seconds.

---

## The Problem

An Austrian SME owner without ERP or accounting software — carpenter, cleanner, business advisor — spends ca. 15-30 unbillable minutes on every quote and invoice. They open a Word template, type line items, calculate totals, add bank details, check the layout. They do this for every job, every week.

The work is not skilled. It is just slow.

**DocAssist** takes a plain-text or spoken job description and produces a structured quote for review, then a compliant invoice on approval — with zero reformatting and zero data re-entry.

---

## Target User

Austrian sole trader or micro-business owner, under 10 employees, no accounting software. Pilot industries: Handwerk / Tischler, Reinigung, Unternehmensberatung.

---

## What "Solved" Looks Like

| Metric | Target | Achieved |
|---|---|---|
| Quote generation time | < 30 seconds | — |
| §11 UStG field compliance | 100% on every invoice | — |
| Quote-to-invoice data entry | Zero additional input | — |
| Owner time per document | < 5 minutes (review + send) | — |
| Cost to serve | < €0.10 per document | — |

*Achieved numbers filled in at project completion.*

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

*To be completed post-deployment.*

```bash
# Clone and install
git clone https://github.com/leonp84/document-agent.git
cd document-agent
pip install -r requirements.txt
cp .env.example .env  # fill in API keys

# Configure your business profile
cp config/business_profile.example.yaml config/business_profile.yaml
# edit business_profile.yaml with your details

# Run locally
uvicorn app.main:app --reload
```

API available at `http://localhost:8000` — see `/docs` for the Swagger UI.

Live demo: *link to be added post-deployment (Cloud Run, europe-west3)*

---

## Eval Report

*To be completed at project completion — will include extraction F1, compliance pass rate, LLM-as-judge quote quality scores, and Sonnet vs Haiku tradeoff numbers.*

See [`docs/eval_results.md`](docs/eval_results.md).

---

## Cost & Latency

*To be completed — will include per-node breakdown, cost per document, and user-month estimates at 20 and 100 docs/month.*

See [`docs/cost_latency.md`](docs/cost_latency.md).

---

## Compliance Standard

Invoice field requirements are derived from §11 UStG and cross-referenced against [ebInterface](https://www.erechnung.gv.at/erb/en_GB/tec_formats_ebinterface), the Austrian Chamber of Commerce's XML standard for e-invoices. ebInterface XML export is planned post-MVP.

---

## Failure Modes

See [`docs/failure_modes.md`](docs/failure_modes.md) for documented failure modes and mitigations (hallucinated line items, wrong VAT rate, silent compliance failure, vague input, price fabrication, unknown client).

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
