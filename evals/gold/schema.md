# Gold Set Schema

Each file contains one JSON object per line (JSONL). Each object is one gold pair.

Target: 25–30 pairs total, roughly split across the three industry files.
Minimum per file: 8 pairs covering the scenario types below.

## Fields

```json
{
  "id": "it_001",
  "industry": "IT",
  "scenario": "rates_explicit",
  "input": "raw job description text — EN or DE, as the owner would type it",
  "expected_scope": {
    "client_ref": "Weber IT Solutions",
    "services": [
      {"description": "Webentwicklung", "quantity": 32, "unit": "Stunden", "rate": 90.00}
    ],
    "vat_rate": 0.20,
    "language": "de",
    "confidence": "high"
  },
  "expected_quote": {
    "line_items": [
      {"description": "Webentwicklung", "qty": 32, "unit": "Stunden", "rate": 90.00, "amount": 2880.00}
    ],
    "net_total": 2880.00,
    "vat_amount": 576.00,
    "gross_total": 3456.00,
    "payment_terms": "Zahlbar innerhalb von 14 Tagen",
    "validity_days": 30
  },
  "notes": "what this pair specifically tests"
}
```

## Scenario Types (cover all of these across each file)

| Scenario | Description |
|---|---|
| `rates_explicit` | All rates present in the input |
| `rates_missing` | No rates in input — system should use business_profile defaults |
| `rates_partial` | Some rates explicit, some missing |
| `english_input` | Input in English rather than German |
| `multi_service` | Three or more distinct service lines |
| `low_confidence` | Vague input — system should ask for clarification |
| `client_partial` | Client name is partial/informal ("Weber" not "Weber IT Solutions GmbH") |
| `edge_materials` | Handwerk: labour + materials with markup |

## Notes on Ground Truth

- `expected_scope` is what the extraction agent should produce from the raw input alone
- `expected_quote` is what a correct, well-structured quote looks like — used as the LLM-as-judge baseline
- Arithmetic in `expected_quote` must be exact: `net_total * vat_rate = vat_amount`, `net_total + vat_amount = gross_total`
- For `rates_missing` scenarios, `expected_scope.services[].rate` should be `null` — the extractor should not invent rates
