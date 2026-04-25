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
