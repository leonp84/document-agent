"""
Model comparison benchmark for scope extraction (Criterion 3).

Runs the full gold set through each configured model, computes all
extraction metrics, and writes docs/tradeoffs.md with a comparison table.

Usage:
    python scripts/compare_models.py              # all models
    python scripts/compare_models.py --tags gpt-oss-20b haiku  # subset
    python scripts/compare_models.py --dry-run    # print config only
"""
import argparse
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).parent.parent

# ── Model configuration ───────────────────────────────────────────────────────

MODEL_CONFIGS: list[dict] = [
    {
        "tag": "gpt-oss-20b",
        "label": "gpt-oss-20b (Windows - RX 9060 XT)",
        "provider": "local",
        "env": {
            "LOCAL_LLM_BASE_URL": "http://127.0.0.1:1234/v1",
            "LOCAL_LLM_MODEL": "openai/gpt-oss-20b",
            "LLM_DISABLE_THINKING": "false",
        },
    },
    {
        "tag": "gemma-4-26b",
        "label": "gemma-4-26b-a4b-it-mlx (MacBook - MLX)",
        "provider": "local",
        "env": {
            "LOCAL_LLM_BASE_URL": "http://192.168.1.181:1234/v1",
            "LOCAL_LLM_MODEL": "gemma-4-26b-a4b-it-mlx",
            "LLM_DISABLE_THINKING": "false",
        },
    },
    {
        "tag": "qwen3.5-35b",
        "label": "qwen3.5-35b-a3b (MacBook - MLX)",
        "provider": "local",
        "skip": True,
        "skip_reason": (
            "Extended thinking cannot be disabled via LM Studio API — reasoning tokens "
            "consume the entire max_tokens budget leaving content empty. "
            "See https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/1559"
        ),
        "env": {},
    },
    {
        "tag": "qwen2.5-coder-14b",
        "label": "Qwen2.5-Coder-14B (MacBook - MLX)",
        "provider": "local",
        "env": {
            "LOCAL_LLM_BASE_URL": "http://192.168.1.181:1234/v1",
            "LOCAL_LLM_MODEL": "qwen/qwen2.5-coder-14b",
            "LLM_DISABLE_THINKING": "false",
        },
    },
    {
        "tag": "claude-haiku",
        "label": "claude-haiku-4-5 (Anthropic API)",
        "provider": "anthropic",
        "env": {
            "DOCASSIST_PROVIDER": "anthropic",
            "ANTHROPIC_MODEL": "claude-haiku-4-5-20251001",
        },
    },
    {
        "tag": "claude-sonnet",
        "label": "claude-sonnet-4-6 (Anthropic API)",
        "provider": "anthropic",
        "env": {
            "DOCASSIST_PROVIDER": "anthropic",
            "ANTHROPIC_MODEL": "claude-sonnet-4-6",
        },
    },
]

# ── Gold data loading ─────────────────────────────────────────────────────────

def _load_gold_pairs() -> list[dict]:
    pairs = []
    for path in sorted((ROOT / "evals" / "gold").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                pairs.append(json.loads(line))
    return pairs

# ── Metric helpers (mirrors evals/test_extraction.py) ────────────────────────

def _client_ref_match(predicted: str, expected: str) -> bool:
    return expected.lower() in predicted.lower() or predicted.lower() in expected.lower()


def _service_description_f1(predicted_services, expected_services: list[dict]) -> float:
    pred_tokens = set(w.lower() for s in predicted_services for w in s.description.split())
    gold_tokens = set(w.lower() for s in expected_services for w in s["description"].split())
    if not gold_tokens:
        return 1.0
    if not pred_tokens:
        return 0.0
    tp = len(pred_tokens & gold_tokens)
    precision = tp / len(pred_tokens)
    recall = tp / len(gold_tokens)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _rate_accuracy(predicted_services, expected_services: list[dict]) -> float:
    explicit = [(s, e) for s, e in zip(predicted_services, expected_services) if e["rate"] is not None]
    if not explicit:
        return 1.0
    correct = sum(
        1 for pred_s, exp_s in explicit
        if pred_s.rate is not None and abs(pred_s.rate - exp_s["rate"]) < 0.01
    )
    return correct / len(explicit)

# ── Per-model benchmark ───────────────────────────────────────────────────────

@dataclass
class ModelResult:
    tag: str
    label: str
    client_accuracy: float = 0.0
    service_f1: float = 0.0
    rate_accuracy: float = 0.0
    null_rate_precision: float = 0.0  # fraction of null-rate entries correctly left null
    low_conf_recall: float = 0.0      # fraction of vague inputs flagged low-confidence
    lang_accuracy: float = 0.0
    elapsed_s: float = 0.0
    pairs_run: int = 0
    parse_errors: int = 0


def _apply_env(config: dict) -> dict:
    """Set env vars for this model and return previous values for restore."""
    saved = {}
    # Always reset provider to local unless overridden
    keys_to_clear = ["DOCASSIST_PROVIDER", "LOCAL_LLM_BASE_URL", "LOCAL_LLM_MODEL",
                     "LLM_DISABLE_THINKING", "ANTHROPIC_MODEL"]
    for k in keys_to_clear:
        saved[k] = os.environ.get(k)
        os.environ.pop(k, None)
    for k, v in config["env"].items():
        saved.setdefault(k, os.environ.get(k))
        os.environ[k] = v
    return saved


def _restore_env(saved: dict) -> None:
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def run_model(config: dict, pairs: list[dict], prompt_version: str = "v3") -> ModelResult:
    from agent.extractor import extract_scope

    result = ModelResult(tag=config["tag"], label=config["label"])
    pairs_with_quote = [p for p in pairs if p["expected_quote"] is not None]
    pairs_low_conf = [p for p in pairs if p["expected_quote"] is None]

    saved = _apply_env(config)
    try:
        print(f"\n  Running {len(pairs)} pairs...")
        t0 = time.time()
        results: dict[str, object] = {}
        for pair in pairs:
            results[pair["id"]] = extract_scope(pair["input"], prompt_version)
            print(f"    {pair['id']}: done")
        result.elapsed_s = time.time() - t0
        result.pairs_run = len(pairs)
        result.parse_errors = sum(
            1 for r in results.values() if not r.client_ref and not r.services
        )

        # Client accuracy
        matched = sum(
            1 for p in pairs_with_quote
            if _client_ref_match(results[p["id"]].client_ref, p["expected_scope"]["client_ref"])
        )
        result.client_accuracy = matched / len(pairs_with_quote) if pairs_with_quote else 0.0

        # Service description F1
        f1_scores = [
            _service_description_f1(results[p["id"]].services, p["expected_scope"]["services"])
            for p in pairs_with_quote
        ]
        result.service_f1 = sum(f1_scores) / len(f1_scores) if f1_scores else 0.0

        # Explicit rate accuracy
        rates_explicit = [p for p in pairs_with_quote if p["scenario"] == "rates_explicit"]
        rate_accs = [
            _rate_accuracy(results[p["id"]].services, p["expected_scope"]["services"])
            for p in rates_explicit
        ]
        result.rate_accuracy = sum(rate_accs) / len(rate_accs) if rate_accs else 0.0

        # Null-rate precision (no hallucinated rates on rates_missing pairs)
        rates_missing = [p for p in pairs_with_quote if p["scenario"] == "rates_missing"]
        no_hallucination = sum(
            1 for p in rates_missing
            if all(s.rate is None for s in results[p["id"]].services)
        )
        result.null_rate_precision = no_hallucination / len(rates_missing) if rates_missing else 1.0

        # Low confidence recall
        low_conf_correct = sum(
            1 for p in pairs_low_conf if results[p["id"]].confidence == "low"
        )
        result.low_conf_recall = low_conf_correct / len(pairs_low_conf) if pairs_low_conf else 1.0

        # Language detection accuracy
        lang_correct = sum(
            1 for p in pairs_with_quote
            if results[p["id"]].language == p["expected_scope"]["language"]
        )
        result.lang_accuracy = lang_correct / len(pairs_with_quote) if pairs_with_quote else 0.0

    finally:
        _restore_env(saved)

    return result

# ── Result store (accumulates across runs) ────────────────────────────────────

_STORE_PATH = ROOT / "docs" / "benchmark_results.json"


def _load_store() -> dict:
    if _STORE_PATH.exists():
        return json.loads(_STORE_PATH.read_text(encoding="utf-8"))
    return {}


def _save_store(store: dict) -> None:
    _STORE_PATH.parent.mkdir(exist_ok=True)
    _STORE_PATH.write_text(json.dumps(store, indent=2), encoding="utf-8")


def _store_result(result: ModelResult) -> None:
    store = _load_store()
    store[result.tag] = {
        "tag": result.tag,
        "label": result.label,
        "client_accuracy": result.client_accuracy,
        "service_f1": result.service_f1,
        "rate_accuracy": result.rate_accuracy,
        "null_rate_precision": result.null_rate_precision,
        "low_conf_recall": result.low_conf_recall,
        "lang_accuracy": result.lang_accuracy,
        "elapsed_s": result.elapsed_s,
        "pairs_run": result.pairs_run,
        "parse_errors": result.parse_errors,
    }
    _save_store(store)


def _results_from_store() -> list[ModelResult]:
    store = _load_store()
    # Return in MODEL_CONFIGS order so the table is consistent
    ordered = []
    for config in MODEL_CONFIGS:
        if config["tag"] in store:
            d = store[config["tag"]]
            r = ModelResult(tag=d["tag"], label=d["label"])
            for field in ("client_accuracy", "service_f1", "rate_accuracy",
                          "null_rate_precision", "low_conf_recall", "lang_accuracy",
                          "elapsed_s", "pairs_run", "parse_errors"):
                setattr(r, field, d[field])
            ordered.append(r)
    return ordered


# ── Markdown report ───────────────────────────────────────────────────────────

_THRESHOLDS = {
    "client_accuracy": 0.85,
    "service_f1": 0.85,
    "rate_accuracy": 0.90,
    "null_rate_precision": 1.0,
    "low_conf_recall": 1.0,
    "lang_accuracy": 1.0,
}


def _cell(value: float, key: str) -> str:
    pct = f"{value:.0%}"
    return pct if value >= _THRESHOLDS.get(key, 0) else f"**{pct}**⚠"


def _write_tradeoffs(prompt_version: str) -> Path:
    out = ROOT / "docs" / "tradeoffs.md"
    out.parent.mkdir(exist_ok=True)

    results = _results_from_store()
    skipped = [c for c in MODEL_CONFIGS if c.get("skip")]

    lines = [
        "# Model Tradeoffs — Scope Extraction",
        "",
        f"Prompt version: `{prompt_version}` - Gold pairs: 24 (8 per industry) - Threshold marked ⚠ if below.",
        "",
        "## Results",
        "",
        "| Model | Client acc | Svc F1 | Rate acc | Null-rate | Low-conf | Lang | Time |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for r in results:
        row = (
            f"| {r.label} "
            f"| {_cell(r.client_accuracy, 'client_accuracy')} "
            f"| {_cell(r.service_f1, 'service_f1')} "
            f"| {_cell(r.rate_accuracy, 'rate_accuracy')} "
            f"| {_cell(r.null_rate_precision, 'null_rate_precision')} "
            f"| {_cell(r.low_conf_recall, 'low_conf_recall')} "
            f"| {_cell(r.lang_accuracy, 'lang_accuracy')} "
            f"| {r.elapsed_s:.0f}s |"
        )
        lines.append(row)

    if skipped:
        lines += ["", "## Skipped Models", ""]
        for c in skipped:
            lines.append(f"**{c['label']}** — {c['skip_reason']}")
            lines.append("")

    lines += [
        "## Decisions",
        "",
        "<!-- Fill in after reviewing the table above. Example: -->",
        "",
        "**Extraction model (Phase 3 / Phase 7):** _TBD based on results._",
        "",
        "**Quote generation model (Phase 5):** _TBD — Sonnet vs Haiku comparison runs in Phase 5._",
        "",
        "**Compliance correction model (Phase 6):** _TBD — likely Haiku (deterministic task, "
        "judged quality expected to be indistinguishable)._",
        "",
        "## Notes",
        "",
        "- Local models tested via LM Studio OpenAI-compatible endpoint.",
        "- Qwen3.5-35b extended thinking disabled (`thinking: disabled`) — "
        "default thinking mode adds ~1800s per call with no quality benefit on structured extraction.",
        "- Cost column omitted for local models (amortised hardware cost). "
        "Anthropic API costs added to `docs/cost_latency.md` in Phase 11.",
    ]

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out

# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Compare extraction models against gold set.")
    parser.add_argument("--tags", nargs="+", help="Run only these model tags")
    parser.add_argument("--prompt-version", default="v3")
    parser.add_argument("--dry-run", action="store_true", help="Print config and exit")
    args = parser.parse_args()

    configs = MODEL_CONFIGS
    if args.tags:
        configs = [c for c in configs if c["tag"] in args.tags]
    if not configs:
        print("No matching model configs found.")
        return

    print(f"Models to run: {[c['tag'] for c in configs]}")
    if args.dry_run:
        for c in configs:
            print(f"\n  {c['tag']}: {c['env']}")
        return

    pairs = _load_gold_pairs()
    print(f"Gold pairs loaded: {len(pairs)}")

    results: list[ModelResult] = []
    for config in configs:
        print(f"\n{'-'*60}")
        print(f"Model: {config['label']}")
        if config.get("skip"):
            print(f"  SKIPPED: {config['skip_reason']}")
            continue
        try:
            result = run_model(config, pairs, args.prompt_version)
            results.append(result)
            _store_result(result)
            print(
                f"  client={result.client_accuracy:.0%}  "
                f"f1={result.service_f1:.2f}  "
                f"rate={result.rate_accuracy:.0%}  "
                f"null={result.null_rate_precision:.0%}  "
                f"lowconf={result.low_conf_recall:.0%}  "
                f"lang={result.lang_accuracy:.0%}  "
                f"time={result.elapsed_s:.0f}s"
            )
        except Exception as e:
            print(f"  FAILED: {e}")

    out = _write_tradeoffs(args.prompt_version)
    print(f"\nTradeoffs doc written -> {out}")


if __name__ == "__main__":
    main()
