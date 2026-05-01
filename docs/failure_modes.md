# Failure Modes & Mitigations — DocAssist

## Core Failure Modes

| Failure | How it manifests | Mitigation | When mitigation doesn't hold |
|---|---|---|---|
| **Hallucinated line items** | LLM invents services not mentioned in the job description | Extraction outputs a structured schema (`ScopeModel`) constrained to what was stated; human review gate surfaces the draft before any invoice is produced | Owner approves without reading carefully. No automated check exists against the original input text. |
| **Wrong VAT rate** | Job type miscategorised, wrong rate applied to the invoice | LLM never decides the VAT rate. Extraction returns the rate stated in the input, or 20% default. The rules engine applies it deterministically — it is never inferred by the model. | Owner states an incorrect rate in the input ("inkl. 10% MwSt" on a standard-rated service). The system trusts explicit input. |
| **Silent compliance failure** | Invoice generated with a missing or malformed §11 UStG field — client cannot reclaim VAT | Deterministic compliance check runs on every invoice. A Haiku correction loop attempts to repair failures (max 2 retries). If repair fails, compliance clarification asks the owner for the missing data. A non-compliant document is never returned. | Compliance correction fires but the Haiku patch introduces a different malformed value. The check re-passes (false positive). This is unlikely for the checked fields but not impossible for free-text fields like `recipient_name_address`. |
| **Vague input hallucination** | Underspecified input ("Mach eine Rechnung") causes the LLM to invent a full scope | Confidence scoring on extraction: if `services` is empty or `client_ref` is missing, confidence is forced to `"low"` regardless of model self-report. Low confidence triggers a clarification request — no quote is generated. | Input is plausible-sounding but wrong (e.g. a job description for the wrong client). Confidence scores `"high"` and extraction proceeds. The human review gate is the last catch. |
| **Price fabrication** | LLM invents rates when none are available | Rate resolution is deterministic. Rates come from: (1) explicit input, (2) `business_profile.yaml` defaults by unit type. If neither applies, the service is flagged `unresolved` and a clarification fires before quote generation. No rate is ever inferred by the model. | Owner provides a rate during clarification that is wrong (e.g. misremembers their day rate). The system trusts the clarified value. |
| **Client not found** | Fuzzy match against `clients.json` fails — invoice has no recipient address | Client lookup returns `None`. The graph continues; the compliance check catches the missing `recipient_name_address` field. The correction loop and/or compliance clarification request the details before a PDF is produced. | Client match returns the wrong record (different company with a similar name). The owner must catch this at human review. |

---

## Known Limitations

**Compliance clarification retry loop has no hard cap.**
After `node_compliance_clarify` fires and the owner provides data, `correction_attempts` resets to 0. If the provided value is invalid (e.g. wrong UID format, date that fails re-parsing), the loop can cycle — `check → correct × 2 → clarify → check → ...` — indefinitely. A `clarification_attempts` counter in state would cap this.

**Non-clarifiable compliance failures route silently to END.**
Only `delivery_date`, `recipient_uid`, and `recipient_name_address` are user-clarifiable. All other failures (VAT arithmetic, line_items, supplier fields) exhaust the correction loop and then route to END with a generic error. Supplier field failures should not occur in practice (always populated from the business profile), but a VAT arithmetic failure would produce a confusing dead-end with no actionable message to the owner.

**Observability gap on compliance dead-end.**
When the graph routes to END after exhausting compliance retries without a clarifiable field, `node_persist` is skipped. That request has no rows in `node_runs`, so it is invisible to the cost and compliance pass-rate queries in `scripts/query.py`.

**Profile visual override does not apply to the PDF.**
`POST /invoice` accepts a `profile_override` body field (brand colour, name, address), but `node_render_pdf` always reads from `config/business_profile.yaml`. The override is accepted but silently ignored.

**Rate overrides apply only to the default fallback.**
The rate modal in the UI sets `rate_overrides` which are merged into `profile.default_rates` for the run. If the owner typed an explicit rate in the job description, that rate is always used — the override has no effect on it. This is correct behaviour but can surprise owners who expect the modal to override everything.
