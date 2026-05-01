# DocAssist — Claude Reference

Read this before touching any code. The build plan (`docassist_build_plan.md`) covers requirements and portfolio criteria. This document covers everything needed to actually work on the codebase.

---

## What the system does

DocAssist takes a plain-text German or English job description from an Austrian SME owner, generates a §11 UStG-compliant quote, lets the owner approve it, then produces a signed-off invoice PDF. The entire pipeline runs inside a LangGraph graph with three human-in-the-loop interrupt points. The API is FastAPI on Cloud Run. The frontend is vanilla JS served from the same container.

**Industries in scope:** Reinigung, Handwerk, Beratung. These drive industry-specific defaults in the business profile.

---

## Directory layout

```
agent/
  graph.py              LangGraph pipeline — state, nodes, routers, build_graph()
  models.py             All Pydantic models (single source of truth)
  extractor.py          Scope extraction LLM call
  quote_generator.py    Quote generation LLM call
  rate_resolver.py      Deterministic rate resolution
  client_lookup.py      Fuzzy client matching (rapidfuzz)
  compliance_engine.py  Deterministic §11 UStG rules engine
  invoice_generator.py  Deterministic quote→invoice mapping
  observability.py      SQLite write path + query helpers
  jobs.py               Job state table (separate from node_runs)

api/
  app.py                FastAPI app, all endpoints, background task helpers

config/
  business_profile.yaml Owner's identity, bank details, default rates
  models.yaml           Which LLM model per graph node

data/
  clients.json          12 synthetic Austrian companies
  docassist.db          SQLite (jobs + node_runs tables)
  invoice_counter.json  Sequential invoice number counter (RE-YYYY-NNN)

prompts/
  scope_extraction/v1.md
  quote_generation/v1.md
  compliance_correction/v1.md

templates/
  de/invoice.html       Jinja2 + WeasyPrint template (German)
  en/invoice.html       Jinja2 + WeasyPrint template (English)

static/
  index.html            Single-page UI
  app.js                All frontend logic (~500 lines, vanilla JS)
  style.css

scripts/
  query.py              Observability report (run with PYTHONPATH=.)
  eval.py               Eval harness
  generate_gold_set.py
  generate_test_invoices.py

tests/
  test_graph.py         Router logic + correction loop + happy path (mocked LLM)
  test_api.py           FastAPI layer — all endpoints, clarification paths
  test_client_lookup.py
  test_compliance_engine.py
  test_rate_resolver.py
  test_quote_generator.py
  test_observability.py

main.py                 uvicorn entrypoint
```

---

## Pydantic models (`agent/models.py`)

All graph state is serialised as dicts (JSON-safe for SQLite checkpointing). Deserialise with `Model.model_validate(state["field"])` before use.

| Model | Purpose | Key fields |
|---|---|---|
| `ServiceLine` | Raw extracted service | description, quantity, unit, rate (null if not stated) |
| `ScopeModel` | Extraction output | client_ref, services[], vat_rate, language, confidence ("high"/"low") |
| `ClientRecord` | Client from clients.json | id, name, short_names[], address_line1/2, uid, email |
| `BusinessProfile` | Owner config from YAML | name, address_line1/2, uid, bank_iban, bank_bic, brand_color, language, industry, default_rates |
| `DefaultRates` | Nested in BusinessProfile | labor_hourly, labor_daily, material_markup_pct |
| `ResolvedServiceLine` | Rate always present | description, quantity, unit, rate (float, never None) |
| `UnresolvedServiceLine` | No rate found | description, quantity, unit |
| `ResolvedScope` | Rate resolution output | client, client_ref, resolved[], unresolved[], vat_rate, language |
| `QuoteLineItem` | Line in quote/invoice | description, qty, unit, rate, amount |
| `QuoteModel` | Quote generation output | client, client_ref, line_items[], net_total, vat_rate, vat_amount, gross_total, payment_terms, language |
| `InvoiceModel` | Full §11 UStG invoice | Everything in QuoteModel + invoice_number, invoice_date, delivery_date, service_period_from/to, supplier_*/recipient_* fields |
| `ComplianceFailure` | Single rule failure | field (string key), reason (human-readable) |
| `ComplianceResult` | Engine output | passed (bool), failures[] |

---

## DocAssistState

```python
class DocAssistState(TypedDict):
    request_id: str
    raw_input: str
    language_override: str | None       # "de"|"en" from UI switcher — applied after extraction
    rate_overrides: dict | None         # e.g. {"labor_hourly": 90.0} — from modal, applied in node_resolve_rates
    scope: dict | None                  # ScopeModel
    client: dict | None                 # ClientRecord
    resolved_scope: dict | None         # ResolvedScope
    quote: dict | None                  # QuoteModel
    approval_status: Literal["pending","approved","rejected"] | None
    approval_feedback: str | None
    invoice: dict | None                # InvoiceModel
    compliance_result: dict | None      # ComplianceResult
    pdf_bytes: bytes | None
    clarifications_needed: Annotated[list[str], operator.add]   # appends, never overwrites
    per_node_metadata: Annotated[list[dict], operator.add]      # appends, never overwrites
    correction_attempts: int
    error: str | None
```

`initial_state(raw_input, language_override=None)` creates a fresh state with all fields zeroed/None. The graph always runs with a checkpointer — `thread_id = request_id`.

---

## LangGraph pipeline — node by node

### node_extract
- Calls `extract_scope(raw_input)` → `(ScopeModel, input_tokens, output_tokens)`
- If `language_override` is set, copies scope with that language (overrides LLM-detected language)
- Writes: `scope`, `per_node_metadata`
- Model: configured in `models.yaml` under `nodes.extract`

### node_scope_clarify *(interrupt)*
- Fires when `scope.confidence == "low"`
- Calls `interrupt({"type": "scope_clarification", "message": "...", "original_input": raw_input})`
- On resume: `answer.get("clarified_input")` becomes new `raw_input`; loops back to `node_extract`
- Writes: `raw_input`, appends to `clarifications_needed`

### node_client_lookup
- Calls `lookup_client(scope.client_ref, clients)` — fuzzy match (rapidfuzz `token_sort_ratio`, threshold 75)
- **Both ref and candidates are lowercased before comparison** (rapidfuzz is case-sensitive by default)
- Returns `None` if no match; graph continues with `client=None` (client unknown but not a blocker)
- Writes: `client`

### node_resolve_rates
- Builds a resolved scope from extracted services
- If `state["rate_overrides"]` is set, merges valid keys into a copy of `profile.default_rates` (does NOT mutate the global profile)
- Resolution logic per service line:
  1. `svc.rate is not None` → use as-is
  2. `svc.unit == "Stunden"` → `profile.default_rates.labor_hourly`
  3. `svc.unit == "Tage"` → `profile.default_rates.labor_daily`
  4. Otherwise → `unresolved` (triggers rate clarification)
- Writes: `resolved_scope`

### node_rate_clarify *(interrupt)*
- Fires when `resolved_scope.unresolved` is non-empty
- Calls `interrupt({"type": "rate_clarification", "message": "...", "services": [description, ...]})`
- On resume: `answer.get("rates")` is `{description: float}` — patches resolved_scope, clears unresolved
- Writes: `resolved_scope`, appends to `clarifications_needed`

### node_generate_quote
- Calls `generate_quote(resolved_scope, rejection_feedback=approval_feedback)` → `(QuoteModel, in_tok, out_tok)`
- Quote generator: LLM receives resolved line items + language; returns formatted descriptions + payment_terms
- If called after rejection, `approval_feedback` is passed to the prompt
- Resets `approval_status="pending"`, clears `approval_feedback`
- Writes: `quote`, `approval_status`, `approval_feedback`, `per_node_metadata`
- Model: `nodes.quote_generate` in models.yaml

### node_human_review *(interrupt)*
- Calls `interrupt({"type": "human_review", "message": "...", "quote": state["quote"]})`
- On resume: `answer.get("status")` → `"approved"` or `"rejected"`; `answer.get("feedback")` → rejection reason
- Writes: `approval_status`, `approval_feedback`

### node_build_invoice
- Deterministic: maps `QuoteModel` fields → `InvoiceModel` via `build_invoice()`
- Assigns next sequential invoice number (`RE-YYYY-NNN`, from `data/invoice_counter.json`)
- Sets `invoice_date = date.today()`, leaves `delivery_date=None` (compliance correction fills it if needed)
- Resets `correction_attempts = 0`
- Writes: `invoice`, `correction_attempts`

### node_check_compliance
- Runs `compliance_check(invoice)` → `ComplianceResult` — fully deterministic, no LLM
- Checks all 11 §11 UStG fields; `recipient_uid` only required if `gross_total > €10,000`
- Writes: `compliance_result`

### node_correct_compliance
- Only fires if compliance failed AND `correction_attempts < 2`
- Calls Haiku LLM with the correction prompt + failure list + current invoice JSON
- LLM returns a JSON patch; `_apply_correction_patch()` applies it to the invoice
- If LLM call fails, invoice is unchanged (compliance will fail again → eventually routes to clarify/END)
- Increments `correction_attempts`
- Writes: `invoice`, `correction_attempts`
- Model: `nodes.compliance_correct` in models.yaml

### node_compliance_clarify *(interrupt)*
- Fires when `correction_attempts >= 2` AND at least one failure field is in `_USER_CLARIFIABLE = {"delivery_date", "recipient_uid", "recipient_name_address"}`
- `_build_clarify_fields(result)` maps failure fields to input specs:
  - `delivery_date` → `{name, input_type: "date"}`
  - `recipient_uid` → `{name, input_type: "text", placeholder: "ATU12345678"}`
  - `recipient_name_address` → three text inputs (name, address_line1, address_line2)
- Calls `interrupt({"type": "compliance_clarification", "message": "...", "fields": [...]})`
- On resume: applies user-provided values directly to invoice (deterministic patch), resets `correction_attempts = 0`
- Writes: `invoice`, `correction_attempts`, appends to `clarifications_needed`

### node_render_pdf
- Calls `render_pdf(invoice, profile)` → PDF bytes via Jinja2 + WeasyPrint
- Template selected by `invoice.language`: `templates/de/invoice.html` or `templates/en/invoice.html`
- **Unit translation**: German units ("Stunden", "Tage", "pauschal") are canonical internally. The EN template translates them via `{% set UNIT = {"Stunden": "hours", ...} %}` at render time
- Writes: `pdf_bytes`

### node_persist
- Writes `per_node_metadata` rows to SQLite `node_runs` table
- Failure here is swallowed — never breaks the pipeline
- Writes: appends to `per_node_metadata` (own timing entry)
- **Note:** this node is only reached on the success path. Compliance dead-end (routed to END) skips it.

---

## Routing

```
START → node_extract
node_extract →(confidence==low)→ node_scope_clarify → node_extract
node_extract →(confidence==high)→ node_client_lookup
node_client_lookup → node_resolve_rates
node_resolve_rates →(unresolved)→ node_rate_clarify → node_generate_quote
node_resolve_rates →(all resolved)→ node_generate_quote
node_generate_quote →(quote set)→ node_human_review
node_generate_quote →(quote None)→ END
node_human_review →(approved)→ node_build_invoice
node_human_review →(rejected)→ node_generate_quote
node_build_invoice → node_check_compliance
node_check_compliance →(passed)→ node_render_pdf
node_check_compliance →(failed, attempts<2)→ node_correct_compliance → node_check_compliance
node_check_compliance →(failed, attempts>=2, clarifiable fields)→ node_compliance_clarify → node_check_compliance
node_check_compliance →(failed, attempts>=2, no clarifiable fields)→ END
node_render_pdf → node_persist → END
```

---

## Interrupt detection — how `_handle_graph_result` works

When `graph.ainvoke()` returns (after hitting any interrupt), the returned state dict does NOT expose the interrupt type directly. Detection always calls `await graph.aget_state(config)` and inspects `snapshot.tasks[i].interrupts[j].value`.

`_extract_clarification(snapshot)` returns the interrupt value dict if `type` is in `{"scope_clarification", "rate_clarification", "compliance_clarification"}`, else `None`.

**Decision tree in `_handle_graph_result`:**
1. `result["error"]` set → `failed`
2. `result["pdf_bytes"]` set → `completed` (store bytes in DB)
3. Otherwise → call `aget_state`:
   - Interrupt found with clarification type → `awaiting_clarification` (store `clarification_json`)
   - No interrupt, `quote` is set, snapshot has pending tasks → `awaiting_approval` (human review)
   - No interrupt, `quote` is set, no pending tasks → `failed` (compliance dead end)
   - No quote, no interrupt → `failed` ("unexpected point")

---

## Job status lifecycle

```
pending → running → awaiting_approval
                 → awaiting_clarification → running → awaiting_approval
                                                    → awaiting_clarification (can repeat)
                                                    → completed
                 → completed
                 → failed
```

**Jobs table columns:** `request_id`, `status`, `quote_json`, `clarification_json`, `pdf_bytes`, `error`, `created_at`, `updated_at`

**Node runs table:** `request_id`, `node`, `timestamp`, `latency_ms`, `model`, `input_tokens`, `output_tokens`, `cost_eur`, `industry_type`, `compliance_passed`, `error`

---

## API endpoints

All non-health endpoints require `X-API-Key` header matching `DOCASSIST_API_KEY` env var.

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | No auth |
| GET | `/config` | Returns `{"api_key": "..."}` — frontend reads this on load |
| GET | `/clients` | Returns `[{name, short_names}]` for the client panel |
| GET | `/profile` | Returns profile fields + `labor_hourly`, `labor_daily` flattened from `default_rates` |
| POST | `/quote` | Body: `{raw_input, language?, rate_overrides?}` → `{request_id}`. Kicks off `_run_graph` as background task |
| GET | `/status/{id}` | Returns `{status, quote, clarification, error}` |
| POST | `/clarify/{id}` | Body: `{clarified_input?, rates?, compliance_data?}`. Resumes graph via `_resume_graph` background task. Resume value = merge of all three fields |
| POST | `/invoice/{id}` | **Returns 202** `{request_id}`. Kicks off `_resume_graph` with `{status:"approved", feedback:None}`. Job must be `awaiting_approval` |
| GET | `/pdf/{id}` | Returns PDF blob from `jobs.pdf_bytes`. Job must be `completed` |

`_run_graph` and `_resume_graph` both call `_handle_graph_result` after `ainvoke` returns. `_resume_graph` takes a generic `resume_value: dict` — the same function handles approval, scope/rate clarification, and compliance clarification resumes.

**Rate limiting:** `POST /quote` is limited to 5/day per IP via `slowapi`.

---

## Frontend (`static/app.js`)

**State variables:**
- `lang`: "de" | "en" — drives all copy and unit translation
- `embeddedKey`: loaded from `GET /config` on page load
- `currentRequestId`: UUID of the active job
- `pollTimer`: `setInterval` handle (2s polling)
- `currentVatRate`: displayed in quote table footer
- `currentClarificationType`: "scope_clarification" | "rate_clarification" | "compliance_clarification" | null
- `isInvoicePhase`: bool — true after Freigeben clicked; changes spinner text, triggers PDF download on complete

**Polling state machine (`pollStatus`):**
- `pending/running/queued` → show spinner (`statusCreating` or `statusInvoice` if `isInvoicePhase`)
- `awaiting_clarification` → stop polling, call `renderClarification(data.clarification)`
- `awaiting_approval` → stop polling, call `renderQuote(data.quote)`
- `failed` → stop polling, show `data.error`
- `completed` + `isInvoicePhase` → `downloadInvoicePdf()` (calls `GET /pdf/{id}`)
- `completed` without invoice phase → show "completed" status text

**Clarification rendering (`renderClarification`):**
- `scope_clarification` → textarea pre-filled with `original_input`
- `rate_clarification` → one number input per service in `clarification.services`
- `compliance_clarification` → inputs per `clarification.fields`: `date` input for `delivery_date`, `text` for others
- All clarification messages are translated by type key (`clarificationMsg_{type}`) — the backend message string is a fallback only

**Unit translation (`fmtUnit`):** Only active when `lang === "en"`. Maps `{Stunden→hours, Tage→days, pauschal→flat rate}`. German units are canonical throughout the pipeline.

**Profile override:** Stored in `localStorage` under key `docassist_profile_override`. Includes text fields (name, address, uid, iban, bic, brand_color) and numeric fields (labor_hourly, labor_daily). Rate fields are sent with `POST /quote` as `rate_overrides`. Other fields are sent with `POST /invoice` as `profile_override` (see known limitations).

---

## Configuration files

**`config/business_profile.yaml`:**
```yaml
name, address_line1, address_line2, uid, logo_path, bank_iban, bank_bic,
brand_color, language (de|en), industry (Reinigung|Handwerk|Beratung)
default_rates:
  labor_hourly: 22     # € — used when no rate in input and unit is Stunden
  labor_daily: 160     # € — used when unit is Tage
  material_markup_pct: 15
```
Loaded once at startup via `_get_profile()` (cached global). Rate overrides per-run are applied as a model_copy in `node_resolve_rates` — the global cache is never mutated.

**`config/models.yaml`:** Maps node names to `provider` ("anthropic"|"local") and `model`. Applied by `_apply_node_env(node_name)` at the start of each LLM node. `_current_model()` reads it back for metadata logging.

---

## LLM provider switching

`DOCASSIST_PROVIDER=anthropic` → Anthropic SDK. `DOCASSIST_PROVIDER=local` → OpenAI-compatible SDK pointed at `LOCAL_LLM_BASE_URL` (LM Studio).

Extractor and quote generator use Anthropic SDK path by default in production. The correction node uses Haiku. Switching providers requires only env var changes — no code changes.

---

## Running the project

```bash
# Tests
pytest tests/

# Observability report (must set PYTHONPATH)
PYTHONPATH=. .venv/Scripts/python scripts/query.py --report

# Dev server
.venv/Scripts/python main.py
# or
uvicorn main:app --reload
```

---

## Known limitations

**1. Profile visual override (brand_color, name etc.) does not apply to the PDF.**
`POST /invoice` accepts a `profile_override` body field and passes it to `InvoiceRequest`, but `_resume_graph` ignores it. The PDF is rendered by `node_render_pdf` using `_get_profile()` (the YAML profile). The re-render logic that existed in the old synchronous `post_invoice` was lost when the endpoint was made async. To fix: store `profile_override_json` in the jobs table when `POST /invoice` is called, and apply it in `_handle_graph_result` when storing `pdf_bytes`.

**2. `node_persist` is skipped on compliance dead-end.**
When `route_after_compliance` returns `END` (non-clarifiable failures, attempts exhausted), the graph goes directly to END without running `node_persist`. That request has no observability row in `node_runs`. To fix: add a `node_dead_end` node that calls `persist_run` before END on that path.

**3. SQLite is ephemeral on Cloud Run.**
Data is lost on container restart. Use `--min-instances=1` for demo continuity. Noted in `api/app.py` lifespan comment.

**4. No bound on compliance clarification retries.**
After `node_compliance_clarify` fires and the user provides data, `correction_attempts` resets to 0. If the provided data is invalid (e.g. wrong UID format), the loop can cycle: `check → correct × 2 → clarify → check → correct × 2 → clarify...` indefinitely. A `clarification_attempts` counter in state would cap this.

**5. Rate overrides apply only to the default fallback.**
If the user types an explicit rate in the input, it is always used — `rate_overrides` has no effect on explicitly stated rates. This is correct behaviour but can surprise users who expect the modal to override everything.

**6. Clarifiable compliance fields are limited.**
Only `delivery_date`, `recipient_uid`, and `recipient_name_address` are user-clarifiable. All other failures (VAT arithmetic, line_items, supplier fields) route to END. Supplier field failures should never occur in practice (always populated from business profile), but VAT arithmetic failures would silently dead-end with a "compliance requirements could not be met" error.

**7. `model` field in `node_runs` was `null` for extraction and quote nodes before this was fixed.**
The fix (`_current_model()` called after `_apply_node_env()`) only applies to new runs.