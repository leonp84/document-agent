"""
End-to-end cost and latency profiler for DocAssist (Phase 11).

Submits test inputs through the HTTP API, auto-approves quotes, then reads
per-node metrics from SQLite. Run once per model configuration to produce the
comparison data for docs/cost_latency.md.

Usage
-----
Start the app first (in a separate terminal):
    .venv/Scripts/python main.py

The POST /quote rate limit (5/day) is automatically bypassed for localhost, so
no config changes are needed before running.

Primary run (models.yaml as shipped — all-Haiku):
    python scripts/profile_pipeline.py --tag primary --api-key $DOCASSIST_API_KEY

Sonnet-quote run:
  1. Edit config/models.yaml — change quote_generate.model to claude-sonnet-4-6
  2. Restart the app
  3. python scripts/profile_pipeline.py --tag sonnet-quote --api-key $DOCASSIST_API_KEY
  4. Revert config/models.yaml

Write docs/cost_latency.md once both runs are complete:
    python scripts/profile_pipeline.py --report

Options
-------
--tag TEXT          Label for this run (stored in docs/profile_results.json).
--api-key TEXT      API key (or set DOCASSIST_API_KEY env var).
--base-url TEXT     App base URL (default: http://localhost:8000).
--poll-interval N   Seconds between status polls (default: 2).
--timeout N         Max seconds to wait per job (default: 120).
--dry-run           Print inputs and exit without submitting.
--report            Write docs/cost_latency.md from stored results.
"""
import argparse
import json
import math
import os
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")
GOLD_DIR = ROOT / "evals" / "gold"
RESULTS_PATH = ROOT / "docs" / "profile_results.json"
REPORT_PATH = ROOT / "docs" / "cost_latency.md"
DB_PATH = ROOT / "data" / "docassist.db"

# ---------------------------------------------------------------------------
# Supplementary inputs — added to gold pairs to reach ~30 total.
# All use explicit rates or units that resolve via business_profile defaults,
# so no clarification interrupt fires.
# ---------------------------------------------------------------------------

_EXTRA_INPUTS: list[dict] = [
    {
        "id": "extra_hw_01",
        "industry": "Handwerk",
        "scenario": "multi_material",
        "input": (
            "Rechnung an Berger Bau GmbH, Dachrinnenreparatur: "
            "1.5 Tage Arbeitsleistung à 480 Euro, Dachrinnen Material 320 Euro, "
            "Dichtmasse und Zubehör 45 Euro"
        ),
    },
    {
        "id": "extra_hw_02",
        "industry": "Handwerk",
        "scenario": "rates_missing",
        "input": (
            "Angebot für Holzbau Steininger KG, Carport Holzkonstruktion Montage, 3 Tage"
        ),
    },
    {
        "id": "extra_bt_01",
        "industry": "Beratung",
        "scenario": "high_value",
        "input": (
            "Angebot für Krenn Holding AG, IT-Strategieberatung Q3: "
            "12 Beratertage à 1200 Euro, Reisekosten pauschal 480 Euro"
        ),
    },
    {
        "id": "extra_bt_02",
        "industry": "Beratung",
        "scenario": "english_rates_explicit",
        "input": (
            "Invoice for Muster Technology GmbH, cloud migration consulting: "
            "8 days at EUR 1100 per day, travel expenses EUR 320"
        ),
    },
    {
        "id": "extra_cl_01",
        "industry": "Reinigung",
        "scenario": "multi_location",
        "input": (
            "Rechnung an ABC Immobilien GmbH, Oktober Reinigung: "
            "Büro 1. Bezirk 16 Stunden à 24 Euro, Lager Simmering 8 Stunden à 22 Euro"
        ),
    },
    {
        "id": "extra_cl_02",
        "industry": "Reinigung",
        "scenario": "rates_missing",
        "input": (
            "Angebot für Neumayr Gastro GmbH, Sonderreinigung nach Veranstaltung, 6 Stunden"
        ),
    },
    {
        "id": "extra_hw_03",
        "industry": "Handwerk",
        "scenario": "rates_explicit_en",
        "input": (
            "Quote for Winkler Construction OG, kitchen cabinet installation: "
            "2 days labour at EUR 460 per day, hardware and fittings EUR 215"
        ),
    },
    {
        "id": "extra_bt_03",
        "industry": "Beratung",
        "scenario": "single_hourly",
        "input": (
            "Rechnung an Pichler Steuerberatung GmbH, Workshop Prozessoptimierung: "
            "6 Stunden à 95 Euro"
        ),
    },
    {
        "id": "extra_cl_03",
        "industry": "Reinigung",
        "scenario": "periodic",
        "input": (
            "Rechnung an Gruber Verwaltung GmbH, monatliche Gebäudereinigung Oktober: "
            "40 Stunden à 22 Euro"
        ),
    },
]


# ---------------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------------

def _load_gold_inputs() -> list[dict]:
    inputs = []
    for path in sorted(GOLD_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            pair = json.loads(line)
            if pair.get("expected_quote") is None:
                continue  # skip low-confidence / clarification-required pairs
            inputs.append(
                {
                    "id": pair["id"],
                    "industry": pair["industry"],
                    "scenario": pair["scenario"],
                    "input": pair["input"],
                }
            )
    return inputs


def load_inputs() -> list[dict]:
    gold = _load_gold_inputs()
    combined = gold + _EXTRA_INPUTS
    return combined


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _headers(api_key: str) -> dict:
    return {"X-API-Key": api_key, "Content-Type": "application/json"}


def submit_job(client: httpx.Client, base_url: str, api_key: str, raw_input: str) -> str:
    resp = client.post(
        f"{base_url}/quote",
        json={"raw_input": raw_input},
        headers=_headers(api_key),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["request_id"]


def poll_status(
    client: httpx.Client,
    base_url: str,
    api_key: str,
    request_id: str,
    poll_interval: float,
    timeout: float,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(
            f"{base_url}/status/{request_id}",
            headers=_headers(api_key),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        status = data["status"]
        if status not in ("pending", "running", "queued"):
            return data
        time.sleep(poll_interval)
    return {"status": "timeout", "quote": None, "clarification": None, "error": "poll timeout"}


def approve_job(
    client: httpx.Client, base_url: str, api_key: str, request_id: str
) -> None:
    resp = client.post(
        f"{base_url}/invoice/{request_id}",
        headers=_headers(api_key),
        timeout=15,
    )
    resp.raise_for_status()


def _auto_answer_clarification(
    client: httpx.Client,
    base_url: str,
    api_key: str,
    request_id: str,
    clarification: dict,
) -> None:
    """Auto-answer any clarification interrupt the pipeline can produce."""
    ctype = clarification.get("type")

    if ctype == "rate_clarification":
        services = clarification.get("services", [])
        rates = {svc: 80.0 for svc in services}
        body: dict = {"rates": rates}

    elif ctype == "compliance_clarification":
        today = time.strftime("%Y-%m-%d")
        defaults = {
            "delivery_date": today,
            "recipient_uid": "ATU00000000",
            "recipient_name": "Profiling Test GmbH",
            "recipient_address_line1": "Teststraße 1",
            "recipient_address_line2": "1010 Wien",
        }
        fields = clarification.get("fields", [])
        compliance_data = {
            f["name"]: defaults[f["name"]]
            for f in fields
            if f.get("name") in defaults
        }
        body = {"compliance_data": compliance_data}

    else:
        raise ValueError(f"unhandled clarification type: {ctype!r}")

    resp = client.post(
        f"{base_url}/clarify/{request_id}",
        json=body,
        headers=_headers(api_key),
        timeout=15,
    )
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# Single-job runner
# ---------------------------------------------------------------------------

_MAX_CLARIFY = 3


def run_one(
    client: httpx.Client,
    base_url: str,
    api_key: str,
    item: dict,
    poll_interval: float,
    timeout: float,
    verbose: bool = True,
) -> dict:
    """
    Run a single job end-to-end through the full pipeline state machine:
      submit → [clarify*] → approve → [clarify*] → complete
    Returns a result dict with keys: id, request_id, status, elapsed_s, error.
    """
    t0 = time.monotonic()
    result = {
        "id": item["id"],
        "industry": item["industry"],
        "scenario": item["scenario"],
        "request_id": None,
        "status": "unknown",
        "elapsed_s": 0.0,
        "error": None,
    }

    try:
        request_id = submit_job(client, base_url, api_key, item["input"])
        result["request_id"] = request_id

        approved = False
        clarify_count = 0

        while True:
            data = poll_status(client, base_url, api_key, request_id, poll_interval, timeout)
            status = data["status"]

            if status == "completed":
                result["status"] = "completed"
                break

            if status in ("failed", "timeout"):
                result["status"] = "failed"
                result["error"] = data.get("error") or status
                break

            if status == "awaiting_approval":
                if approved:
                    result["status"] = "failed"
                    result["error"] = "unexpected second awaiting_approval"
                    break
                approve_job(client, base_url, api_key, request_id)
                approved = True
                continue

            if status == "awaiting_clarification":
                if clarify_count >= _MAX_CLARIFY:
                    result["status"] = "skipped"
                    result["error"] = f"too many clarifications ({clarify_count})"
                    break
                clarification = data.get("clarification") or {}
                ctype = clarification.get("type")
                try:
                    _auto_answer_clarification(client, base_url, api_key, request_id, clarification)
                except ValueError as exc:
                    result["status"] = "skipped"
                    result["error"] = str(exc)
                    break
                clarify_count += 1
                continue

            result["status"] = "failed"
            result["error"] = f"unexpected status: {status!r}"
            break

    except httpx.HTTPStatusError as exc:
        result["status"] = "failed"
        result["error"] = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = str(exc)

    result["elapsed_s"] = round(time.monotonic() - t0, 2)

    if verbose:
        icon = "✓" if result["status"] == "completed" else "✗"
        print(
            f"  {icon} {item['id']:20s}  "
            f"{result['status']:20s}  "
            f"{result['elapsed_s']:5.1f}s"
            + (f"  [{result['error']}]" if result["error"] else "")
        )

    return result


# ---------------------------------------------------------------------------
# Metrics from SQLite
# ---------------------------------------------------------------------------

def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_v = sorted(values)
    idx = max(0, math.ceil(len(sorted_v) * pct / 100) - 1)
    return sorted_v[idx]


def collect_metrics(request_ids: list[str]) -> dict:
    """Query node_runs for the given request_ids and compute per-node and document stats."""
    if not request_ids:
        return {}
    placeholders = ",".join("?" * len(request_ids))
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            f"""SELECT node, latency_ms, cost_eur, input_tokens, output_tokens
                FROM node_runs
                WHERE request_id IN ({placeholders})""",
            request_ids,
        ).fetchall()

        doc_rows = conn.execute(
            f"""SELECT request_id, SUM(latency_ms), SUM(cost_eur)
                FROM node_runs
                WHERE request_id IN ({placeholders})
                GROUP BY request_id""",
            request_ids,
        ).fetchall()
    finally:
        conn.close()

    # Per-node aggregation
    by_node: dict[str, dict[str, list]] = defaultdict(
        lambda: {"latency": [], "cost": [], "in_tok": [], "out_tok": []}
    )
    for node, latency, cost, in_tok, out_tok in rows:
        n = by_node[node]
        n["latency"].append(latency)
        if cost is not None:
            n["cost"].append(cost)
        if in_tok is not None:
            n["in_tok"].append(in_tok)
        if out_tok is not None:
            n["out_tok"].append(out_tok)

    node_stats = {}
    for node, d in sorted(by_node.items()):
        lat = d["latency"]
        node_stats[node] = {
            "calls": len(lat),
            "avg_ms": round(sum(lat) / len(lat), 1) if lat else 0,
            "p50_ms": round(_percentile(lat, 50), 1),
            "p95_ms": round(_percentile(lat, 95), 1),
            "avg_cost_eur": round(sum(d["cost"]) / len(d["cost"]), 6) if d["cost"] else None,
            "avg_in_tok": round(sum(d["in_tok"]) / len(d["in_tok"])) if d["in_tok"] else None,
            "avg_out_tok": round(sum(d["out_tok"]) / len(d["out_tok"])) if d["out_tok"] else None,
        }

    # Per-document aggregation
    doc_latencies = [row[1] for row in doc_rows if row[1] is not None]
    doc_costs = [row[2] for row in doc_rows if row[2] is not None]

    doc_stats = {
        "doc_count": len(doc_rows),
        "avg_latency_ms": round(sum(doc_latencies) / len(doc_latencies), 1) if doc_latencies else 0,
        "p50_latency_ms": round(_percentile(doc_latencies, 50), 1),
        "p95_latency_ms": round(_percentile(doc_latencies, 95), 1),
        "avg_cost_eur": round(sum(doc_costs) / len(doc_costs), 6) if doc_costs else None,
        "p95_cost_eur": round(_percentile(doc_costs, 95), 6) if doc_costs else None,
    }

    return {"nodes": node_stats, "document": doc_stats}


# ---------------------------------------------------------------------------
# Results store
# ---------------------------------------------------------------------------

def _load_results() -> dict:
    if RESULTS_PATH.exists():
        return json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    return {}


def _save_results(store: dict) -> None:
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(store, indent=2), encoding="utf-8")


def store_run(tag: str, run_results: list[dict], metrics: dict) -> None:
    store = _load_results()
    completed = [r for r in run_results if r["status"] == "completed"]
    store[tag] = {
        "tag": tag,
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "request_ids": [r["request_id"] for r in run_results if r["request_id"]],
        "inputs_run": len(run_results),
        "completed": len(completed),
        "failed": sum(1 for r in run_results if r["status"] == "failed"),
        "skipped": sum(1 for r in run_results if r["status"] == "skipped"),
        "metrics": metrics,
    }
    _save_results(store)
    print(f"\nResults saved to {RESULTS_PATH}")


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------

_DISPLAY_NODES = [
    "node_extract",
    "node_client_lookup",
    "node_resolve_rates",
    "node_generate_quote",
    "node_build_invoice",
    "node_check_compliance",
    "node_correct_compliance",
    "node_render_pdf",
    "node_persist",
]

_NODE_LABELS = {
    "node_extract": "Scope extraction",
    "node_client_lookup": "Client lookup",
    "node_resolve_rates": "Rate resolution",
    "node_generate_quote": "Quote generation",
    "node_build_invoice": "Invoice build",
    "node_check_compliance": "Compliance check",
    "node_correct_compliance": "Compliance correction",
    "node_render_pdf": "PDF render",
    "node_persist": "Persist",
}


def _ms(v: float | None) -> str:
    return f"{v:.0f}" if v is not None else "—"


def _eur(v: float | None) -> str:
    return f"€{v:.6f}" if v is not None else "—"


def _tok(v: int | None) -> str:
    return str(v) if v is not None else "—"


def write_report(fx_rate: float = 0.92) -> None:
    store = _load_results()
    if not store:
        print("No profile results found. Run the profiler first.")
        return

    tags = list(store.keys())
    runs = [store[t] for t in tags]

    lines: list[str] = [
        "# Cost & Latency Profile — DocAssist",
        "",
        "Generated from live profiling runs against the local server.",
        f"FX rate used: 1 USD = {fx_rate} EUR.",
        "",
    ]

    # Run summary
    lines += ["## Run Summary", ""]
    lines += ["| Tag | Docs run | Completed | Failed | Skipped | Run at |"]
    lines += ["|---|---|---|---|---|---|"]
    for run in runs:
        lines.append(
            f"| `{run['tag']}` "
            f"| {run['inputs_run']} "
            f"| {run['completed']} "
            f"| {run['failed']} "
            f"| {run['skipped']} "
            f"| {run['run_at']} |"
        )

    # Per-document summary
    lines += ["", "## Per-Document Totals", ""]
    lines += ["| Tag | Avg latency | p50 | p95 | Avg cost | p95 cost |"]
    lines += ["|---|---|---|---|---|---|"]
    for run in runs:
        doc = run["metrics"].get("document", {})
        lines.append(
            f"| `{run['tag']}` "
            f"| {_ms(doc.get('avg_latency_ms'))} ms "
            f"| {_ms(doc.get('p50_latency_ms'))} ms "
            f"| {_ms(doc.get('p95_latency_ms'))} ms "
            f"| {_eur(doc.get('avg_cost_eur'))} "
            f"| {_eur(doc.get('p95_cost_eur'))} |"
        )

    # Per-node breakdown for each tag
    for run in runs:
        nodes = run["metrics"].get("nodes", {})
        lines += [
            "",
            f"## Per-Node Breakdown — `{run['tag']}`",
            "",
            "| Node | Calls | Avg (ms) | p50 (ms) | p95 (ms) | Avg cost | Avg in tok | Avg out tok |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for node_key in _DISPLAY_NODES:
            if node_key not in nodes:
                continue
            n = nodes[node_key]
            label = _NODE_LABELS.get(node_key, node_key)
            lines.append(
                f"| {label} "
                f"| {n['calls']} "
                f"| {_ms(n['avg_ms'])} "
                f"| {_ms(n['p50_ms'])} "
                f"| {_ms(n['p95_ms'])} "
                f"| {_eur(n.get('avg_cost_eur'))} "
                f"| {_tok(n.get('avg_in_tok'))} "
                f"| {_tok(n.get('avg_out_tok'))} |"
            )

    # Cost at scale (uses first run's avg cost as baseline)
    if runs:
        baseline_tag = "primary" if "primary" in store else tags[0]
        baseline_cost = store[baseline_tag]["metrics"].get("document", {}).get("avg_cost_eur")
        if baseline_cost is not None:
            lines += [
                "",
                "## Cost at Scale",
                "",
                f"Based on `{baseline_tag}` avg cost per document: **{_eur(baseline_cost)}**",
                "",
                "| Monthly volume | Est. monthly cost |",
                "|---|---|",
            ]
            for vol in (10, 20, 50, 100, 500):
                monthly = baseline_cost * vol
                lines.append(f"| {vol} docs/month | {_eur(monthly)} |")

    # Model decisions
    lines += [
        "",
        "## Model Decisions",
        "",
        "See `docs/tradeoffs.md` for extraction model comparison (Haiku vs Sonnet vs local models).",
        "",
    ]
    if len(runs) >= 2:
        costs = {
            run["tag"]: run["metrics"].get("document", {}).get("avg_cost_eur")
            for run in runs
        }
        lines += [
            "Quote generation comparison:",
            "",
        ]
        for tag, cost in costs.items():
            lines.append(f"- **`{tag}`**: avg {_eur(cost)} per document")
        lines.append("")
        lines += [
            "**Decision:** See tradeoff rationale in the run data above.",
            "Haiku is chosen for all nodes unless a specific quality gap is documented in `tradeoffs.md`.",
        ]
    else:
        lines += [
            "Run with `--tag sonnet-quote` (Sonnet for quote generation) to populate the comparison.",
        ]

    lines.append("")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report written to {REPORT_PATH}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="DocAssist pipeline profiler.")
    parser.add_argument("--tag", default="primary", help="Label for this run")
    parser.add_argument("--api-key", default=os.environ.get("DOCASSIST_API_KEY", ""), help="API key")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--limit", type=int, default=0, help="Run only the first N inputs (0 = all)")
    parser.add_argument("--dry-run", action="store_true", help="Print inputs and exit")
    parser.add_argument("--report", action="store_true", help="Write docs/cost_latency.md and exit")
    args = parser.parse_args()

    if args.report:
        write_report()
        return

    inputs = load_inputs()
    if args.limit > 0:
        inputs = inputs[: args.limit]

    if args.dry_run:
        print(f"Inputs to run: {len(inputs)}")
        for i, item in enumerate(inputs, 1):
            print(f"  {i:2d}. [{item['industry']:12s}] {item['id']:20s}  {item['input'][:70]}")
        return

    if not args.api_key:
        print("Error: --api-key required (or set DOCASSIST_API_KEY)", file=sys.stderr)
        sys.exit(1)

    print(f"Tag: {args.tag}")
    print(f"Base URL: {args.base_url}")
    print(f"Inputs: {len(inputs)}")
    print(f"Poll interval: {args.poll_interval}s  Timeout: {args.timeout}s")
    print()

    # Health check
    try:
        with httpx.Client() as client:
            resp = client.get(f"{args.base_url}/health", timeout=5)
            resp.raise_for_status()
        print("Health check passed.\n")
    except Exception as exc:
        print(f"Health check failed: {exc}\nIs the app running at {args.base_url}?", file=sys.stderr)
        sys.exit(1)

    print(f"{'ID':22s}  {'Status':20s}  {'Time':>6s}")
    print("-" * 60)

    run_results: list[dict] = []
    with httpx.Client() as client:
        for item in inputs:
            result = run_one(
                client, args.base_url, args.api_key, item,
                args.poll_interval, args.timeout,
            )
            run_results.append(result)

    completed_ids = [r["request_id"] for r in run_results if r["status"] == "completed" and r["request_id"]]
    metrics = collect_metrics(completed_ids)

    total = len(run_results)
    n_ok = sum(1 for r in run_results if r["status"] == "completed")
    n_fail = sum(1 for r in run_results if r["status"] == "failed")
    n_skip = sum(1 for r in run_results if r["status"] == "skipped")
    avg_cost = metrics.get("document", {}).get("avg_cost_eur")

    print(f"\n{'─' * 60}")
    print(f"  Completed: {n_ok}/{total}   Failed: {n_fail}   Skipped: {n_skip}")
    if avg_cost is not None:
        print(f"  Avg cost per document: €{avg_cost:.6f}")
    avg_lat = metrics.get("document", {}).get("avg_latency_ms")
    if avg_lat:
        print(f"  Avg end-to-end latency: {avg_lat:.0f} ms")
    print()

    store_run(args.tag, run_results, metrics)
    print(f"Run '{args.tag}' complete. Use --report to write docs/cost_latency.md.")


if __name__ == "__main__":
    main()
