# Model Tradeoffs — Scope Extraction

Prompt version: `v3` - Gold pairs: 24 (8 per industry) - Threshold marked ⚠ if below.

## Results

| Model | Client acc | Svc F1 | Rate acc | Null-rate | Low-conf | Lang | Time |
|---|---|---|---|---|---|---|---|
| gpt-oss-20b (Windows - RX 9060 XT) | 100% | 93% | 100% | 100% | 100% | 100% | 64s |
| gemma-4-26b-a4b-it-mlx (MacBook - MLX) | 100% | 91% | 100% | 100% | 100% | 100% | 164s |
| Qwen2.5-Coder-14B (MacBook - MLX) | 100% | 95% | 100% | 100% | 100% | 100% | 217s |
| claude-haiku-4-5 (Anthropic API) | 100% | 94% | 100% | 100% | 100% | 100% | 35s |
| claude-sonnet-4-6 (Anthropic API) | 100% | 94% | 100% | 100% | 100% | 100% | 62s |

## Skipped Models

**qwen3.5-35b-a3b (MacBook - MLX)** — Extended thinking cannot be disabled via LM Studio API — reasoning tokens consume the entire max_tokens budget leaving content empty. See https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/1559

## Decisions

**Extraction model (Phase 3 / Phase 7):** claude-haiku-4-5. Highest F1 among API models (0.94), fastest wall-clock (35s for 24 pairs), and indistinguishable from Sonnet on every metric. Local models are competitive on accuracy but slower and carry infra overhead in production.

**Quote generation model (Phase 5):** TBD — Sonnet vs Haiku comparison runs in Phase 5 against the quote gold set.

**Compliance correction model (Phase 6):** claude-haiku-4-5. Deterministic task with a structured repair prompt; judged quality expected to be indistinguishable from Sonnet at a fraction of the cost.

## Notes

- Local models tested via LM Studio OpenAI-compatible endpoint.
- Qwen3.5-35b skipped: LM Studio does not honour `chat_template_kwargs: {enable_thinking: false}` — the reasoning chain consumes the entire token budget before producing output.
- Cost column omitted for local models (amortised hardware cost). Anthropic API costs added to `docs/cost_latency.md` in Phase 11.
