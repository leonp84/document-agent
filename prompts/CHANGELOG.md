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
