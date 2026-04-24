# Project 3 — Build Plan & UX Overview

This document consolidates the scoping discussion for the Contract / Invoice Review Agent (Project 3 from `ai_portfolio_project_brief.md`). It covers: V1 scope decisions, what the user experiences, how the agentic system works, and the phased build plan with time estimates.

---

## V1 Scope Decisions

Locked before building begins:

- **Documents in scope:** contracts AND invoices (both types accepted via the same upload endpoint; classifier routes internally).
- **Contract scope:** full review against an authored ruleset (`rules.yaml`). No user setup required.
- **Invoice scope:** internal checks only. No contract-pairing in V1.
  - *Always on:* math errors, duplicate line items, unusual payment terms per ruleset, total mismatch.
  - *Not in V1:* rate mismatch vs agreed contract, vendor-specific baselines. These would require the user to upload a related contract or set up a vendor profile — deferred to V1.1.
- **Clause coverage (starter set):** payment terms, auto-renewal, termination notice, liability cap, price escalation. Expand if time allows.
- **Data sourcing:** CUAD for contract extraction evals, synthetic generator for invoice evals, small hand-curated gold set (WKO Musterverträge + real invoices, redacted) for sanity.

---

## What the User Experiences

The landing page at (e.g.) `www.document-agent.ai` is deliberately minimal — this is an API-first product, so the UI is scaffolding, not the deliverable:

- One-sentence problem statement: "First-pass review for vendor contracts and invoices — flagged anomalies with cited reasoning, in under a minute."
- A single upload widget that takes a PDF.
- Optional: an email field to receive the memo, or a request-ID for polling.
- Link to `/docs` (FastAPI's Swagger UI) for developers who want to integrate.

The user uploads a vendor contract or invoice. The page shows "Reviewing… (~45s)" with a progress indicator. When done, they receive a **structured review memo** in the browser (and optionally by email):

### For a contract:
- One-paragraph plain-language summary.
- Key terms extracted as a table (parties, effective date, term length, payment terms, notice period, liability cap, auto-renewal).
- Flagged items with severity, the exact quoted clause, and a reasoning paragraph.
- Recommended actions ("Ask vendor to reduce payment terms to 30 days"; "Consult a lawyer before signing — this liability cap is unusually low").
- Explicit scope disclaimer: "First-pass review only. Not legal advice."

### For an invoice:
- Summary: vendor, total, due date.
- Line items extracted as a table.
- Flags: math errors, duplicate lines, unusual payment terms, total mismatches (all internal checks only in V1).
- Recommended actions.

**What the user achieves:** a 4-hour manual review compressed to 5 minutes of reading a structured memo. Every flag is traceable to a specific clause, so they can verify the claim without re-reading the document.

---

## How the Agentic System Produces This

```
       PDF upload (FastAPI /review)
                │
                ▼
      ┌──────────────────────┐
      │  Parse + Classify    │  pdfplumber → text; LLM decides
      │  (contract/invoice)  │  document type; routes to schema
      └──────────┬───────────┘
                 ▼
      ┌──────────────────────┐
      │  Extraction Agent    │  Sonnet + Pydantic schema
      │  → structured state  │  (parties, dates, amounts, clauses)
      └──────────┬───────────┘
                 ▼
      ┌──────────────────────┐
      │  Rules Engine        │  Deterministic Python
      │  → list[Violation]   │  Checks ruleset, no LLM
      └──────────┬───────────┘
                 ▼
      ┌──────────────────────┐
      │  Risk-Reasoning      │  Haiku per violation
      │  Agent (per flag)    │  → reasoning + cited quote
      └──────────┬───────────┘
                 ▼
      ┌──────────────────────┐
      │  Cite-Check          │  Deterministic: does the
      │  (verification)      │  quote appear in the source?
      └──────────┬───────────┘
                 │
          unverified? ──yes──► loop back to Risk-Reasoning
                │              (max 2 retries, then drop flag)
                 │ no
                 ▼
      ┌──────────────────────┐
      │  Memo Drafter        │  Template-filled Markdown
      │  → final review memo │  + Haiku for summary paragraph
      └──────────┬───────────┘
                 ▼
      ┌──────────────────────┐
      │  Response            │  Persist to SQLite (obs logs)
      │  (API + email)       │  Return memo or request-ID
      └──────────────────────┘
```

### Why this shape fits LangGraph

- The **cite-check loop** is a real conditional branch, not decoration — it genuinely fails claims and routes back to re-reason, and the loop count is logged per request.
- The **per-violation fan-out** for risk reasoning is a natural LangGraph pattern (map over violations, aggregate results).
- **State is explicit:** extraction, violations, reasonings, unverified_claims, and memo all live in a TypedDict that every node sees.

---

## Where "Typical" Comes From

An important distinction that's easy to get wrong:

### The `/data` folder is for evals, NOT runtime reference.

CUAD + synthetic invoices are the **ruler** (how accurate is our extractor?), not the **yardstick** user docs get compared against at runtime.

### V1's only source of "typical" is the authored ruleset (`rules.yaml`).

Example rule:

```yaml
- name: long_payment_terms
  predicate: extraction.payment_days > 60
  severity: medium
  rationale: "Typical SME vendor terms are net-30. Longer terms shift cash-flow risk to the buyer."
```

Each rule is a documented judgment call. Flags become defensible because the rationale ships with the output:

> "Payment term is 90 days. Our ruleset flags terms > 60 days as unusual for SME vendor contracts — longer terms shift cash-flow risk to you."

### Out of scope for V1:

- **Statistical norms from a corpus.** Tempting (e.g. "95th percentile payment term is 75 days from CUAD") but CUAD is mostly US commercial — distribution doesn't transfer to Austrian SME vendor contracts. Skip.
- **User-supplied context for invoice rate-matching.** Deferred to V1.1 (vendor profiles or contract-pairing).

The ruleset rationale itself gets documented in `docs/ruleset_rationale.md` so portfolio reviewers can audit the choices.

---

## Build Phases

Total budget: ~100–150 hours across 8–12 weeks.

The key framing: **criterion 2 says evals must predate tuning.** The sequence is NOT "build agent → add evals." It's "build data → build eval harness → build each component against its eval." The git history itself is part of the portfolio artifact — a reviewer scrolling the log should see `feat: eval harness` land *before* `feat: extraction agent`.

### Phase 0 — Problem Definition

**What it's doing:** Producing the README-grade problem statement before writing code.

**What it requires:**
- Final decision on clause scope (already committed: payment terms, auto-renewal, termination notice, liability cap, price escalation).
- Write: one-paragraph problem statement, one-paragraph target user, bulleted "what solved looks like" with the brief's numbers as targets.

**Deliverable:** `README.md` Section 1 filled in. Commit #1 of the portfolio.

**Time:** 3–5 hours.

---

### Phase 1 — Data Foundation

**What it's doing:** Securing the documents and their ground truth *before* any LLM call exists.

**What it requires:**
- Pull CUAD; filter to contracts containing the target clause types.
- Write a synthetic invoice generator in pure Python. Template-based, parameterized (vendor, line items, rates, totals). Perturbation mode injects known anomalies (rate mismatch, duplicate line, extended payment terms) and records ground truth.
- Curate a small gold set by hand: ~15 real contracts (WKO Musterverträge) + ~10 real invoices (redacted) as a sanity anchor.
- 30-minute spike to pick a PDF parsing library (`pdfplumber`, `unstructured`, `pypdf`).

**Deliverable:** `data/` folder with raw docs + parallel `annotations/` folder of ground-truth JSON. A CLI tool to regenerate the synthetic invoice set from a seed.

**Time:** 8–12 hours.

---

### Phase 2 — Eval Harness (Before Any LLM Call)

**What it's doing:** Writing the ruler before anything to measure. The commit `feat: eval harness` lands before `feat: extraction agent`.

**What it requires:**
- Design metrics: extraction → precision/recall per field; anomaly → precision/recall/FP rate; reasoning quality → LLM-as-judge rubric (pairwise comparison, not absolute scoring); latency → p50/p95 per stage; cost → $ per doc broken down per LLM call.
- Build: pytest-based harness that loads the test set, runs stub functions, computes metrics, prints a scorecard. Stubs return dummy values so the harness runs end-to-end before any real implementation.
- Build: `scripts/eval.py` entry point producing a JSON report committed to the repo (score history visible in git).

**Deliverable:** `pytest -q evals/` runs in <5 seconds against stubs and returns a numerical scorecard. Scorecard format locked; real implementations plug in.

**Time:** 6–10 hours.

---

### Phase 3 — Extraction Agent (First Real LLM Component)

**What it's doing:** First defensible model/prompt choice with numbers from the project's own test set.

**What it requires:**
- Design Pydantic output schemas per document type (structured-output discipline, no free-text parsing).
- Build extraction using Claude's native structured output / tool-use. Sonnet first to establish the quality ceiling, then Haiku to see where it breaks. Keep eval numbers for both; write the comparison.
- Iterate: prompts live in `/prompts/extraction/v1.md`, `v2.md`, etc. Each version gets an eval run. `prompts/CHANGELOG.md` notes what changed between versions and the metric delta (criterion 4).
- Decide chunking strategy for long contracts: no chunking (200k context), semantic sections, or fixed-size with overlap. Test at least two; record the tradeoff.

**Deliverable:** an `extractor` module taking a parsed doc and returning a validated Pydantic object. Evals show ≥95% F1 on target clause types. `docs/tradeoffs.md` with Sonnet-vs-Haiku and chunking-strategy numbers.

**Time:** 15–25 hours. Prompt iteration eats more than expected.

---

### Phase 4 — Rules-Check Layer

**What it's doing:** A deterministic layer — proves maturity by NOT using an LLM where a rule engine works.

**What it requires:**
- Design rule schema (YAML). Rules operate on the extracted Pydantic object, not raw text. Each rule: name, predicate, severity, human-readable description, rationale.
- Build a rule evaluator that iterates rules over the extraction, emits a list of violations with references back to the triggering clause.
- Write 15–25 rules covering the clause scope + invoice internal checks (math, duplicates, totals).
- Eval: synthetic invoice generator's known anomalies give direct ground truth for precision/recall.
- Write `docs/ruleset_rationale.md` explaining why each rule exists and where the threshold comes from.

**Deliverable:** `rules.yaml` + `rules_engine.py` with full test coverage. Eval numbers for anomaly detection against synthetic set.

**Time:** 6–10 hours.

---

### Phase 5 — Risk-Reasoning Agent

**What it's doing:** The one place where the LLM genuinely has to explain something. Also where the hallucinated-citation failure mode lives.

**What it requires:**
- Design: the prompt takes a single violation + the source clause and produces a reasoning paragraph with an explicit citation (quoted text). Output schema includes the citation field separately so it can be checked programmatically.
- Build: post-hoc cite verification — a deterministic check that the cited quote actually appears in the source. This feeds the LangGraph verification branch.
- Build: LLM-as-judge eval for reasoning quality. Pairwise comparison prompt; judge with Opus or a different Sonnet prompt to reduce self-preference bias.
- Validate the judge itself: sample 20 judged pairs, score manually, compute agreement rate. Target: >80% agreement. If lower, the rubric needs work.

**Deliverable:** reasoning module + cite-verifier + LLM-as-judge harness. Criterion 6 artifact starts forming ("hallucinated citations" failure mode has a real mitigation).

**Time:** 15–25 hours. LLM-as-judge validation is where novices skip and seniors don't.

---

### Phase 6 — LangGraph Assembly

**What it's doing:** Composing the pieces into the formal graph.

**What it requires:**
- Design state schema (TypedDict): `raw_text`, `document_type`, `extraction`, `violations`, `reasonings`, `unverified_claims`, `memo`, per-node metadata (timing, cost, model).
- Build nodes for each prior phase + edges. Conditional branch: cite-check failure routes back to reasoning with failed claims as context. Cap at 2 revision loops.
- Build model-per-node configuration so Haiku can be swapped in for specific nodes without code changes.

**Deliverable:** a runnable graph. `graph.invoke({doc: ...})` produces a full state. Eval harness now runs the full graph end-to-end, not stubs.

**Time:** 10–15 hours.

---

### Phase 7 — Memo Drafting

**What it's doing:** The final humanization layer. Structured state becomes a document a human reads.

**What it requires:**
- Design memo template (Markdown): summary, key terms extracted, anomalies flagged with reasoning, recommended next actions, scope disclaimer.
- Build a deterministic template filler + a small LLM call (Haiku) for the executive summary paragraph only. Resist letting the LLM draft the whole memo — that loses the structural guarantees from earlier phases.

**Deliverable:** a memo renderer. Output is reproducible given the same state.

**Time:** 5–8 hours.

---

### Phase 8 — Observability

**What it's doing:** Criterion 5 in a can. The thing that makes the system credible for production.

**What it requires:**
- LangSmith integration for tracing (free tier covers this).
- SQLite schema for per-request logs: request_id, timestamp, input_hash, per-node latency, per-node model, per-node token counts, per-node cost, final output hash, error. One row per node, linked by request_id.
- Build `scripts/query.py` or `notebooks/observability.ipynb` with 5–6 canned queries (p95 latency per node, cost distribution, error rate by node, model-choice impact). Criterion 5's pass test is "you can answer ops questions in under a minute" — these canned queries are the answer.

**Deliverable:** SQLite DB accumulating real data + LangSmith traces + one notebook/script demonstrating queryability.

**Time:** 10–15 hours.

---

### Phase 9 — FastAPI Deployment

**What it's doing:** Criterion 7. Localhost doesn't count.

**What it requires:**
- API design: `POST /review` with file upload, `GET /review/{id}` for async status, `GET /healthz`. Async from the start — review takes ~45s, so sync endpoints are hostile.
- Auth: API key header middleware. Single hardcoded key in env var is fine for V1 — criterion 7 says "auth," not "full OAuth."
- Rate limiting: `slowapi` or similar. Per-key limits.
- Background job queue: `BackgroundTasks` in FastAPI + SQLite for job state. Don't reach for Celery/Redis unless showing off infra.
- Deploy: Railway or Render. Both have free-ish tiers, both take ~10 minutes once the Dockerfile is right. Get a real URL.

**Deliverable:** a URL a reviewer can hit with `curl` using an API key and get a valid response. A 20-line README section with the curl command.

**Time:** 10–15 hours.

---

### Phase 10 — Cost & Latency Profiling

**What it's doing:** Criterion 8 writeup. The data already exists from Phase 8; this phase is the analysis.

**What it requires:**
- Compute: per-node latency distributions, per-node cost distributions, dollar cost per document, user-month cost at 50 docs/month.
- Run: the same 50 documents through an all-Haiku variant AND the Sonnet-where-it-matters variant. Record both.
- Write `docs/cost_latency.md` with tables and tradeoff story. Example: "We use Sonnet at extraction because Haiku's F1 dropped from 0.95 to 0.81 on payment terms. We use Haiku for memo summary because judged quality was indistinguishable."

**Deliverable:** a markdown doc with real tables and defended choices. One of the most valuable portfolio artifacts because almost nobody produces it.

**Time:** 5–8 hours.

---

### Phase 11 — Failure Modes & README Polish

**What it's doing:** Criteria 1 and 6. Narrative work that's been deferred.

**What it requires:**
- Write `docs/failure_modes.md` covering the four failure modes: silent extraction errors, over-flagging, hallucinated citations, scope creep to legal advice. For each: how it manifests, how the system catches it (eval + runtime check), what happens when it does.
- Write README final pass: problem, target user, what solved looks like (with *achieved* numbers, not targets), quickstart, architecture diagram, link to eval report, link to cost/latency report, link to failure modes doc, link to hosted API.
- Record a 2–3 minute Loom walkthrough. Most reviewers will watch the video before reading the code.

**Deliverable:** a repo that reads like a production system's docs, not a project writeup.

**Time:** 3–5 hours.

---

## What the Overall Sequence Requires Personally

Zooming out from phase-by-phase:

- **Discipline to stage eval commits before implementation commits.** Hardest part psychologically — the cool thing is tempting to build first. Don't. The git history is a portfolio artifact.
- **Comfort writing prompts as a first-class deliverable.** Version them, changelog them, tie each version to an eval run.
- **Willingness to write deterministic code where LLMs would be tempting.** The rules engine, cite-check, synthetic generator — these are where seniority shows.
- **Patience with LLM-as-judge validation.** Tempting to skip; don't.
- **Willingness to keep real logs and look at them.** Nobody's going to enforce Phase 10. You have to want it.

---

## Open Questions for Tomorrow's Review

Flag anything below that needs revisiting before building:

- Is the 5-clause starter set (payment terms, auto-renewal, termination notice, liability cap, price escalation) the right scope, or should it expand/contract?
- Is "invoice = internal checks only" acceptable for V1, or does it feel too thin for the portfolio story?
- Is the 8–12 week / 100–150 hour budget realistic given your side-project hours?
- PDF parsing library choice — is there a preference before the 30-minute spike?
- Deployment target — Railway, Render, or something else (Fly, AWS)?
- Does "document-agent" stay as the working name, or is a real product name coming?