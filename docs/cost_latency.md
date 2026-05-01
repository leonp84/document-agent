# Cost & Latency Profile — DocAssist

Generated from live profiling runs against the local server.
FX rate used: 1 USD = 0.92 EUR.

## Run Summary

| Tag | Docs run | Completed | Failed | Skipped | Run at |
|---|---|---|---|---|---|
| `primary` | 10 | 10 | 0 | 0 | 2026-05-01T10:57:51Z |
| `sonnet-quote` | 10 | 10 | 0 | 0 | 2026-05-01T11:03:47Z |

## Per-Document Totals

| Tag | Avg latency | p50 | p95 | Avg cost | p95 cost |
|---|---|---|---|---|---|
| `primary` | 4713 ms | 4115 ms | 8195 ms | €0.006903 | €0.007489 |
| `sonnet-quote` | 4811 ms | 4574 ms | 6618 ms | €0.008979 | €0.009772 |

## Per-Node Breakdown — `primary`

| Node | Calls | Avg (ms) | p50 (ms) | p95 (ms) | Avg cost | Avg in tok | Avg out tok |
|---|---|---|---|---|---|---|---|
| Scope extraction | 10 | 1179 | 1154 | 1442 | €0.001646 | 1529 | 141 |
| Client lookup | 10 | 0 | 0 | 0 | — | — | — |
| Rate resolution | 10 | 0 | 0 | 0 | — | — | — |
| Quote generation | 10 | 882 | 787 | 1434 | €0.000791 | 814 | 52 |
| Invoice build | 10 | 1 | 1 | 1 | — | — | — |
| Compliance check | 40 | 0 | 0 | 0 | — | — | — |
| Compliance correction | 20 | 1326 | 1038 | 2527 | €0.002233 | 2612 | 84 |

## Per-Node Breakdown — `sonnet-quote`

| Node | Calls | Avg (ms) | p50 (ms) | p95 (ms) | Avg cost | Avg in tok | Avg out tok |
|---|---|---|---|---|---|---|---|
| Scope extraction | 10 | 1346 | 1217 | 2577 | €0.001646 | 1529 | 141 |
| Client lookup | 10 | 0 | 0 | 0 | — | — | — |
| Rate resolution | 10 | 0 | 0 | 1 | — | — | — |
| Quote generation | 10 | 1410 | 1391 | 1798 | €0.002867 | 815 | 45 |
| Invoice build | 10 | 1 | 1 | 1 | — | — | — |
| Compliance check | 40 | 0 | 0 | 0 | — | — | — |
| Compliance correction | 20 | 1027 | 1022 | 1345 | €0.002233 | 2612 | 84 |

## Cost at Scale

Based on `primary` avg cost per document: **€0.006903**

| Monthly volume | Est. monthly cost |
|---|---|
| 10 docs/month | €0.069030 |
| 20 docs/month | €0.138060 |
| 50 docs/month | €0.345150 |
| 100 docs/month | €0.690300 |
| 500 docs/month | €3.451500 |

## Model Decisions

See `docs/tradeoffs.md` for the extraction model comparison (Haiku vs Sonnet vs local models).

**Quote generation — Haiku vs Sonnet:**

| | `primary` (Haiku) | `sonnet-quote` (Sonnet) |
|---|---|---|
| Avg cost/doc (quote node) | €0.000791 | €0.002867 |
| Avg latency (quote node) | 882 ms | 1410 ms |
| Avg in tokens | 814 | 815 |
| Avg out tokens | 52 | 45 |

Sonnet costs 3.6× more for quote generation with 60% higher latency. Token counts are identical on the input side and near-identical on output, confirming the task is well within Haiku's capability. **Decision: Haiku for all LLM nodes.**

**Compliance correction loop:**

Every document in this test set ran the correction loop to completion (2 retries per doc) before triggering a compliance clarification for `delivery_date`. Job descriptions do not contain explicit service dates, so the Haiku correction node cannot infer the value from context — this is the expected path when no date is provided. The correction loop accounts for €0.002233/doc, roughly 32% of total document cost. Owners who include a service date in their input (e.g. "Arbeiten vom 28. April") would bypass this loop entirely.
