# AI Engineering Portfolio Project Brief

## Purpose

This document is a briefing for exploring and scoping one of two candidate portfolio projects.
The goal is a single, well-engineered AI system that (1) demonstrates production-grade AI engineering
discipline to potential employers and technical co-founders, and (2) serves as a credible technical
starting point for an SME-focused AI startup context.

The project must pass all 8 criteria defined below. These are the difference between a toy demo
and a portfolio project that signals genuine engineering competence.

---

## The 8 Portfolio Criteria (Pass/Fail)

Each criterion includes a pass test and a fail test.

### 1. Specified problem, not a tool demo
- **Pass:** README opens with the problem, the user, and what "solved" looks like — with concrete numbers (latency budget, cost ceiling, quality threshold). Someone reading only the README can tell what was being optimised for.
- **Fail:** README reads "I built an AI X."

### 2. Eval suite that predates optimisation work
- **Pass:** A test set covering typical cases, edge cases, and adversarial inputs, with metrics that track what matters for the problem. Commit history shows evals landing before tuning begins. Suite is runnable today and returns a number.
- **Fail:** "Evaluation" consists of a handful of screenshots or manual spot-checks.

### 3. Model and component choices with documented tradeoffs
- **Pass:** Embedding model, chunking strategy, LLM choice — each defensible with numbers from your own test set, not blog posts or defaults.
- **Fail:** Any choice answers "it's what the tutorial used."

### 4. Prompts as versioned code
- **Pass:** Prompts in separate files or a management layer, changes tracked, linked to eval results. You can diff prompt v3 vs v4 and state which performed better and by how much.
- **Fail:** `grep` finds prompt fragments scattered across the codebase.

### 5. Structured observability
- **Pass:** Per-request logs with inputs, outputs, latency, cost, tool calls, and errors — in a queryable format (SQLite, JSONL, or a logging tool). You can answer "what's the p95 latency on queries of type X?" in under a minute.
- **Fail:** Debugging means rerunning the request with print statements.

### 6. Documented failure modes and mitigations
- **Pass:** A README section or dedicated doc listing how the system fails (timeouts, hallucinated outputs, context overrun, retrieval misses) and what is done about each. Someone reading it would trust you to debug their production system.
- **Fail:** Failure mode = "sometimes it gives weird answers."

### 7. Deployed beyond localhost
- **Pass:** A real API (FastAPI), hosted somewhere accessible, with auth, rate limiting, and error handling. A reviewer can hit an endpoint.
- **Fail:** The only way to see it work is to clone the repo and run it locally.

### 8. Cost and latency profiled, not guessed
- **Pass:** Real numbers per request, broken down by component. You know what one user-month costs. Ideally a cheaper model or prompt variant has been tested and the tradeoff quantified.
- **Fail:** You don't know what it costs to run.

---

## Stack Constraints

- **Orchestration:** LangGraph (non-negotiable — demonstrating framework familiarity is part of the goal)
- **Language:** Python
- **LLM APIs:** Anthropic Claude primary (Sonnet-class for quality, Haiku-class for cost comparison)
- **Observability:** LangSmith for tracing; SQLite or JSONL for persistent request logs
- **Deployment:** FastAPI + cloud host (Railway, Render, or AWS — TBD)
- **Evals:** Custom eval suite; LLM-as-judge for quality dimensions where ground truth is subjective
- **Vector store:** ChromaDB (local dev) → cloud option TBD for production
- **Prompt management:** Versioned files in `/prompts/` directory, tracked in git

---

## Candidate Projects

Two projects are in scope. One will be selected and built to completion.
The selection criterion is: which one produces cleaner evals, more motivated LangGraph usage,
and the most defensible "what does solved mean" answer?

---

### Project Option 1 — RFP / Tender Response Drafter

**The problem:**
Service businesses (agencies, consultancies, technical contractors) spend significant unbilled time
responding to incoming RFPs and Ausschreibungen. Responses are largely templated, yet each requires
extracting a requirements matrix from the RFP, matching it against the company's capability library
and past proposals, and drafting a structured response per section. The work is high-stakes,
deadline-driven, and currently done manually.

**Target user:**
A single SME service business (5–50 employees) that responds to 3–15 RFPs per month.
Austrian/DACH context is a natural fit (public-sector Ausschreibungen are well-documented).

**What "solved" looks like:**
- Given an RFP document (PDF or DOCX), the system produces a draft response that addresses ≥90%
  of explicit requirements, cites real capability evidence from the company library, and follows
  the RFP's own section structure.
- Latency: full draft in under 90 seconds.
- Cost: under €1.50 per draft.
- Human review time reduced from ~4 hours to ~45 minutes of editing.

**LangGraph motivation:**
The pipeline has genuinely distinct stages that benefit from explicit state management:
1. Requirement extraction agent (parse RFP → structured requirements matrix)
2. Retrieval agent (match requirements → capability library chunks)
3. Drafting agent (per-section response generation)
4. Critique agent (review draft against requirements matrix, flag gaps)
5. Revision agent (address critique, produce final draft)

This is a planner/sub-agent pattern with conditional branching (e.g. low-confidence
retrieval triggers a clarification step), which is exactly what LangGraph is designed for.

**Eval story:**
- Did the draft address every explicit requirement? (precision/recall against extracted matrix)
- Did it cite real capability evidence rather than hallucinate past projects?
- Did it follow the RFP's section structure?
- Adversarial test: RFP with requirements outside the company's stated capabilities —
  does the system flag gaps rather than fabricate coverage?

**Primary failure modes to demonstrate:**
- Hallucinated past projects (critical — must be caught by eval suite)
- Requirement misses (silent failure — the dangerous kind)
- Boilerplate drift across sections (quality regression)

**Data sourcing:**
Austrian public-sector Ausschreibungen (Bundesvergabegesetz portal) for RFP inputs.
Synthetic capability library for a fictional consultancy. No real client data required.

---

### Project Option 3 — Contract / Invoice Review Agent

**The problem:**
Austrian micro-businesses and SMEs regularly receive vendor contracts and supplier invoices
containing terms they don't have time to read carefully: unusual payment terms, auto-renewal
clauses, liability caps, price escalation clauses, or invoice line items that don't match
agreed rates. A first-pass review that extracts key terms, checks against a reference
template or ruleset, and flags anomalies with cited reasoning reduces risk without requiring
a lawyer for every document.

**Target user:**
An Austrian SME owner or office manager processing 10–50 vendor documents per month.
Positioned explicitly as "first-pass review to brief a human" — not legal advice.

**What "solved" looks like:**
- Given a contract or invoice (PDF), the system extracts all key terms with document citations,
  flags anomalies against a ruleset with reasoning, and produces a structured review memo.
- Extraction accuracy: ≥95% on a ground-truth annotated test set for standard clause types.
- False positive rate on anomaly flagging: ≤20% (too many false alarms destroys trust).
- Latency: review memo in under 60 seconds.
- Cost: under €0.30 per document.

**LangGraph motivation:**
- Extraction agent (structured output: parties, amounts, dates, key clauses)
- Rules-check agent (compare extracted terms against configurable ruleset)
- Risk-reasoning agent (for each flagged item: explain why it's unusual, cite the clause)
- Memo-drafting agent (produce human-readable review document)
- Optional: verification loop — draft reasoning → check each claim against source text →
  revise if unsupported (demonstrates trust-boundary thinking)

**Eval story:**
Ground truth is unusually clean here:
- Extraction accuracy against manually annotated test contracts (precision/recall per field type)
- Anomaly detection: annotated test set with known unusual clauses — did the system flag them?
- Reasoning quality: did the flagged reasoning actually follow from the cited clause?
  (LLM-as-judge with a strict rubric)
- Adversarial set: correct contracts with no anomalies — false positive rate
- Refusal test: "is this legal?" style questions — does the system stay in scope?

**Primary failure modes to demonstrate:**
- Silent extraction errors (missed clause that was present — worst case)
- Over-flagging (destroys user trust faster than under-flagging)
- Hallucinated clause citations (states a clause exists that isn't in the document)
- Scope creep (starts giving legal opinions rather than flagging for human review)

**Data sourcing:**
Publicly available Austrian standard contract templates (Musterverträge from WKO),
synthetic vendor invoices. No real client data required. DSGVO-relevant framing
adds realism without requiring actual client documents.

**Startup relevance:**
This project maps directly to "proactive admin assistant for Austrian micro-businesses."
Invoice processing, contract term extraction, and anomaly flagging are the exact
administrative tasks the target user drowns in. The portfolio project is simultaneously
a credibility artifact and a working feature prototype.

---

## Suggested First Conversation with Claude Code

Ask Claude Code to do the following:

1. Review both candidate projects and recommend one based on:
   - Which produces the cleaner, more objective eval suite
   - Which has the more naturally motivated LangGraph graph structure
   - Which is more achievable as a solo project in 8–12 weeks
   - Which better serves as an SME startup proof-of-concept

2. For the recommended project, produce:
   - A proposed directory structure
   - A LangGraph state schema (TypedDict) for the main graph
   - A skeleton eval harness (pytest-based) with placeholder test cases
   - A `/prompts/` directory structure with versioning convention
   - A FastAPI skeleton with request logging middleware
   - A `README.md` template with the problem statement and success metrics pre-filled

3. Flag any assumptions that need validation before building begins,
   particularly around data sourcing, chunking strategy choices,
   and the LLM-as-judge rubric for quality evaluation.

---

## Non-Goals (Explicit Scope Boundaries)

- No fine-tuning. Prompt engineering and RAG are the optimisation levers.
- No multi-tenant architecture. Single user/organisation for v1.
- No UI beyond a minimal FastAPI-served frontend or Gradio wrapper.
  The API is the deliverable; the UI is optional scaffolding.
- No real client data. All development uses synthetic or public-domain documents.
- No "general purpose" scope creep. The project solves one problem for one user type.