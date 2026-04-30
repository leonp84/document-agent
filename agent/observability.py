"""SQLite observability — write path, cost calculation, and query helpers."""
import sqlite3
from pathlib import Path

_DEFAULT_DB = Path(__file__).parent.parent / "data" / "docassist.db"

# Anthropic token pricing USD/token (mid-2025 published rates)
# Replace input/output prices with measured actuals after Phase 11 profiling run.
_PRICES: dict[str, tuple[float, float]] = {
    "haiku":  (0.80 / 1_000_000,  4.00 / 1_000_000),
    "sonnet": (3.00 / 1_000_000, 15.00 / 1_000_000),
    "opus":   (15.00 / 1_000_000, 75.00 / 1_000_000),
}
_USD_TO_EUR = 0.92  # approximate — update in Phase 11 with FX rate from .env


def init_db(db_path: Path = _DEFAULT_DB) -> None:
    """Create the node_runs table and indexes if they don't exist."""
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS node_runs (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id        TEXT    NOT NULL,
                node              TEXT    NOT NULL,
                timestamp         REAL    NOT NULL,
                latency_ms        REAL    NOT NULL,
                model             TEXT,
                input_tokens      INTEGER,
                output_tokens     INTEGER,
                cost_eur          REAL,
                industry_type     TEXT,
                compliance_passed INTEGER,
                error             TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_request ON node_runs(request_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_node    ON node_runs(node)")
    conn.close()


def token_cost_eur(
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
) -> float | None:
    """
    Return cost in EUR for a single LLM call.
    Returns None when token counts are unavailable.
    Returns 0.0 for local / unrecognised models.
    """
    if model is None or input_tokens is None or output_tokens is None:
        return None
    model_lower = model.lower()
    for key, (in_price, out_price) in _PRICES.items():
        if key in model_lower:
            usd = (input_tokens * in_price) + (output_tokens * out_price)
            return round(usd * _USD_TO_EUR, 8)
    return 0.0  # local model or unrecognised


def persist_run(
    request_id: str,
    per_node_metadata: list[dict],
    industry_type: str | None = None,
    compliance_passed: bool | None = None,
    error: str | None = None,
    db_path: Path = _DEFAULT_DB,
) -> None:
    """
    Write one row per entry in per_node_metadata to the SQLite store.
    compliance_passed and error apply to the whole run — stamped on every row
    so queries can filter without a JOIN.
    """
    init_db(db_path)
    compliance_int = (1 if compliance_passed else 0) if compliance_passed is not None else None
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            for meta in per_node_metadata:
                model = meta.get("model")
                in_tok = meta.get("input_tokens")
                out_tok = meta.get("output_tokens")
                conn.execute(
                    """INSERT INTO node_runs
                       (request_id, node, timestamp, latency_ms, model,
                        input_tokens, output_tokens, cost_eur,
                        industry_type, compliance_passed, error)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        request_id,
                        meta["node"],
                        meta["timestamp"],
                        meta["latency_ms"],
                        model,
                        in_tok,
                        out_tok,
                        token_cost_eur(model, in_tok, out_tok),
                        industry_type,
                        compliance_int,
                        error,
                    ),
                )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Query helpers — used by scripts/query.py
# ---------------------------------------------------------------------------

def _rows(db_path: Path, sql: str, params: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def p95_latency_per_node(db_path: Path = _DEFAULT_DB) -> list[dict]:
    """Return p95 latency (ms) for each node, sorted slowest first."""
    rows = _rows(db_path, "SELECT node, latency_ms FROM node_runs ORDER BY node, latency_ms")
    from collections import defaultdict
    by_node: dict[str, list[float]] = defaultdict(list)
    for node, latency in rows:
        by_node[node].append(latency)
    result = []
    for node, latencies in sorted(by_node.items()):
        idx = max(0, int(len(latencies) * 0.95) - 1)
        result.append({"node": node, "p95_ms": latencies[idx], "count": len(latencies)})
    return sorted(result, key=lambda r: r["p95_ms"], reverse=True)


def avg_cost_per_document(db_path: Path = _DEFAULT_DB) -> float | None:
    """Average total cost per request (EUR). None if no cost data recorded yet."""
    rows = _rows(
        db_path,
        "SELECT request_id, SUM(cost_eur) FROM node_runs WHERE cost_eur IS NOT NULL GROUP BY request_id",
    )
    if not rows:
        return None
    total = sum(r[1] for r in rows)
    return round(total / len(rows), 6)


def compliance_pass_rate(db_path: Path = _DEFAULT_DB) -> float | None:
    """Fraction of completed runs where compliance passed (0.0–1.0). None if no data."""
    rows = _rows(
        db_path,
        """SELECT compliance_passed
           FROM node_runs
           WHERE node = 'node_check_compliance' AND compliance_passed IS NOT NULL""",
    )
    if not rows:
        return None
    passed = sum(1 for (v,) in rows if v == 1)
    return round(passed / len(rows), 4)


def clarification_trigger_rate(db_path: Path = _DEFAULT_DB) -> float | None:
    """Fraction of runs that triggered at least one clarification branch. None if no data."""
    total_rows = _rows(db_path, "SELECT COUNT(DISTINCT request_id) FROM node_runs")
    total = total_rows[0][0] if total_rows else 0
    if total == 0:
        return None
    clarified_rows = _rows(
        db_path,
        """SELECT COUNT(DISTINCT request_id) FROM node_runs
           WHERE node IN ('node_scope_clarify', 'node_rate_clarify')""",
    )
    clarified = clarified_rows[0][0] if clarified_rows else 0
    return round(clarified / total, 4)


def cost_by_industry(db_path: Path = _DEFAULT_DB) -> list[dict]:
    """Average per-document cost (EUR) broken down by industry_type."""
    rows = _rows(
        db_path,
        """SELECT industry_type, request_id, SUM(cost_eur)
           FROM node_runs
           WHERE cost_eur IS NOT NULL
           GROUP BY industry_type, request_id""",
    )
    from collections import defaultdict
    by_industry: dict[str, list[float]] = defaultdict(list)
    for industry, _req, cost in rows:
        by_industry[industry or "unknown"].append(cost)
    return [
        {"industry": k, "avg_cost_eur": round(sum(v) / len(v), 6), "doc_count": len(v)}
        for k, v in sorted(by_industry.items())
    ]


def token_usage_by_model(db_path: Path = _DEFAULT_DB) -> list[dict]:
    """Total input and output tokens per model."""
    rows = _rows(
        db_path,
        """SELECT model, SUM(input_tokens), SUM(output_tokens), COUNT(*)
           FROM node_runs
           WHERE model IS NOT NULL
           GROUP BY model
           ORDER BY SUM(input_tokens) DESC""",
    )
    return [
        {"model": model, "total_input": inp or 0, "total_output": out or 0, "calls": count}
        for model, inp, out, count in rows
    ]
