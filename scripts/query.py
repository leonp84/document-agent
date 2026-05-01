"""Canned observability queries for the DocAssist SQLite store.

Usage:
    .venv/Scripts/python -m scripts/query.py --report          # full report
    python scripts/query.py --latency         # p95 latency per node
    python scripts/query.py --cost            # avg cost per document
    python scripts/query.py --compliance      # compliance pass rate
    python scripts/query.py --clarifications  # clarification branch rate
    python scripts/query.py --industry        # cost by industry
    python scripts/query.py --tokens          # token usage by model
"""
import argparse
from pathlib import Path

from agent.observability import (
    avg_cost_per_document,
    clarification_trigger_rate,
    compliance_pass_rate,
    cost_by_industry,
    p95_latency_per_node,
    token_usage_by_model,
)

_DB = Path(__file__).parent.parent / "data" / "docassist.db"


def _section(title: str) -> None:
    print(f"\n{'─' * 52}")
    print(f"  {title}")
    print(f"{'─' * 52}")


def report_latency(db: Path) -> None:
    _section("p95 Latency per node (ms)")
    rows = p95_latency_per_node(db)
    if not rows:
        print("  No data yet.")
        return
    for r in rows:
        print(f"  {r['node']:<35} {r['p95_ms']:>8.1f} ms  ({r['count']} calls)")


def report_cost(db: Path) -> None:
    _section("Average cost per document (EUR)")
    cost = avg_cost_per_document(db)
    if cost is None:
        print("  No cost data yet — run with DOCASSIST_PROVIDER=anthropic to capture token costs.")
    else:
        print(f"  €{cost:.6f} per document")


def report_compliance(db: Path) -> None:
    _section("Compliance pass rate")
    rate = compliance_pass_rate(db)
    if rate is None:
        print("  No compliance data yet.")
    else:
        print(f"  {rate * 100:.1f}%  ({rate:.4f})")


def report_clarifications(db: Path) -> None:
    _section("Clarification branch trigger rate")
    rate = clarification_trigger_rate(db)
    if rate is None:
        print("  No data yet.")
    else:
        print(f"  {rate * 100:.1f}% of runs triggered at least one clarification")


def report_industry(db: Path) -> None:
    _section("Average cost by industry (EUR)")
    rows = cost_by_industry(db)
    if not rows:
        print("  No data yet.")
        return
    for r in rows:
        print(f"  {r['industry']:<20} €{r['avg_cost_eur']:.6f}  ({r['doc_count']} docs)")


def report_tokens(db: Path) -> None:
    _section("Token usage by model")
    rows = token_usage_by_model(db)
    if not rows:
        print("  No token data yet.")
        return
    for r in rows:
        print(
            f"  {r['model']:<45} "
            f"in={r['total_input']:>8,}  out={r['total_output']:>8,}  "
            f"calls={r['calls']:>5}"
        )


def full_report(db: Path) -> None:
    print("\n╔══════════════════════════════════════════════════════╗")
    print("║          DocAssist — Observability Report            ║")
    print("╚══════════════════════════════════════════════════════╝")
    report_latency(db)
    report_cost(db)
    report_compliance(db)
    report_clarifications(db)
    report_industry(db)
    report_tokens(db)
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="DocAssist observability queries")
    parser.add_argument("--report",         action="store_true", help="Full report (all queries)")
    parser.add_argument("--latency",        action="store_true", help="p95 latency per node")
    parser.add_argument("--cost",           action="store_true", help="Average cost per document")
    parser.add_argument("--compliance",     action="store_true", help="Compliance pass rate")
    parser.add_argument("--clarifications", action="store_true", help="Clarification branch rate")
    parser.add_argument("--industry",       action="store_true", help="Cost by industry")
    parser.add_argument("--tokens",         action="store_true", help="Token usage by model")
    parser.add_argument("--db",             default=str(_DB),    help="Path to SQLite database")
    args = parser.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"Database not found: {db}")
        print("Run the pipeline at least once to generate data.")
        return

    if args.report or not any([
        args.latency, args.cost, args.compliance,
        args.clarifications, args.industry, args.tokens,
    ]):
        full_report(db)
        return

    if args.latency:        report_latency(db)
    if args.cost:           report_cost(db)
    if args.compliance:     report_compliance(db)
    if args.clarifications: report_clarifications(db)
    if args.industry:       report_industry(db)
    if args.tokens:         report_tokens(db)
    print()


if __name__ == "__main__":
    main()
