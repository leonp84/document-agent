# Prompt Changelog

Tracks prompt version → eval score → what changed.
Run `python scripts/eval.py --suite extraction` to generate scores.

---

## scope_extraction/v1.md — baseline

**Date:** 2026-04-25
**Model (local dev):** gemma-4-26b-a4b-it-mlx
**Model (production):** TBD — Claude Sonnet/Haiku (Phase 10)

| Metric | Score | Threshold | Pass |
|---|---|---|---|
| client_ref accuracy | ≥85% | 85% | yes |
| service description F1 | 0.84 | 0.85 | **no** |
| explicit rate accuracy | ≥90% | 90% | yes |
| missing rates left null | 100% | 100% | yes |
| low confidence triggered | 1/4 (25%) | 100% | **no** |
| language detection | 100% | 100% | yes |
| eval pass rate | 10/12 (83%) | 100% | no |

**Model (local dev):** openai/gpt-oss-20b on Windows desktop RX 9060 XT (~59 tok/sec)
**Run time:** 70s for 24 pairs

**What changed:** Initial prompt. System role + task definition, JSON schema, no-hallucination
rate rule, 3 few-shot examples (DE explicit rates, EN missing rate, DE vague/low-confidence).

**Failures to fix in v2:**
- Service F1 0.84 just below threshold — model paraphrases descriptions instead of extracting verbatim. Fix: add explicit "copy description exactly as written" instruction.
- Low confidence not triggered for hw_002, hw_008, cl_004 — vague inputs treated as high confidence. Fix: stronger, more explicit low-confidence rule with examples of what "vague" means.

---

## scope_extraction/v2.md

**Date:** 2026-04-25
**Model (local dev):** openai/gpt-oss-20b on Windows desktop RX 9060 XT (~59 tok/sec)

| Metric | Score | Threshold | Pass |
|---|---|---|---|
| client_ref accuracy | ≥85% | 85% | yes |
| service description F1 | 0.84 | 0.85 | **no** |
| explicit rate accuracy | ≥90% | 90% | yes |
| missing rates left null | 100% | 100% | yes |
| low confidence triggered | 4/4 (100%) | 100% | yes |
| language detection | 100% | 100% | yes |
| eval pass rate | 11/12 (92%) | 100% | no |

**What changed from v1:** Added verbatim extraction instruction ("Copy the service description EXACTLY word for word"). Added explicit low-confidence rules with examples of vague inputs. Fixed low-confidence failures.

**Failures to fix in v3:**
- Service F1 0.82–0.84 for English inputs — gold set had German descriptions for `language: "en"` entries (bt_004, cl_003, hw_004). This is a gold set bug, not a model limitation.

---

## scope_extraction/v3.md

**Date:** 2026-04-25
**Model (local dev):** openai/gpt-oss-20b on Windows desktop RX 9060 XT (~59 tok/sec)

| Metric | Score | Threshold | Pass |
|---|---|---|---|
| client_ref accuracy | 100% | 85% | yes |
| service description F1 | ≥0.85 | 0.85 | yes |
| explicit rate accuracy | 100% | 90% | yes |
| missing rates left null | 100% | 100% | yes |
| low confidence triggered | 4/4 (100%) | 100% | yes |
| language detection | 100% | 100% | yes |
| eval pass rate | 12/12 (100%) | 100% | yes |

**Run time:** ~66s for 24 pairs

**What changed from v2:** Added "keep description in same language as input — do not translate" rule. Fixed gold set: English-input entries (bt_004, cl_003, hw_004) now have English descriptions in `expected_scope.services` to match the prompt's language-preservation rule.

---

## quote_generation/v2.md

**Date:** 2026-04-30
**Model:** claude-haiku-4-5-20251001

**What changed from v1:** Strengthened Rule 3 to explicitly target quantity/unit phrases embedded in raw descriptions (e.g. "12 Laufmeter", "48 m²", "6 Stunden à EUR 75/h"). Rule 3 in v1 said "do not include numbers, quantities, rates" but the model kept embedded quantity phrases because they were part of the raw description string it received. Added Example 5 with a realistic multi-line Handwerk input showing the exact strip pattern — the worked example is the primary signal since rule text alone was insufficient. Also strengthened Rule 5 to capture explicit payment terms from the input rather than always defaulting.

**Observed failure in v1:** "Trennwand errichten, 12 Laufmeter inkl. Material" passed through with "12 Laufmeter" intact. Quantity/unit columns already carry that data — redundancy looks unprofessional on the document.

---

## compliance_correction/v1.md — baseline

**Date:** 2026-04-26
**Model:** claude-haiku-4-5-20251001
**Eval:** not yet run — no synthetic compliance-correction test set exists. Eval suite to be added in Phase 9.

**Design decisions:**
- Returns a fixed seven-field JSON patch; graph merges non-null values into the InvoiceModel.
- Only the seven fields an LLM can plausibly infer from a job description are correctable. Supplier fields, arithmetic fields, and line items are explicitly out of scope.
- `delivery_date` failure triggers a branch decision inside the prompt: single-day job → `delivery_date`; range → `service_period_from` + `service_period_to`. This keeps the routing logic in the prompt rather than requiring an extra graph node.
- Five worked examples cover: single date, monthly period, recipient from text, recipient UID from text, and the cannot-infer case. The cannot-infer example is deliberate — it teaches the model to return null rather than guess.
