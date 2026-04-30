"""Unit tests for agent.observability — schema, write path, cost calculation, queries."""
import sqlite3
from pathlib import Path

import pytest

from agent.observability import (
    avg_cost_per_document,
    clarification_trigger_rate,
    compliance_pass_rate,
    cost_by_industry,
    init_db,
    p95_latency_per_node,
    persist_run,
    token_cost_eur,
    token_usage_by_model,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path) -> Path:
    """Isolated in-memory-backed SQLite file per test."""
    return tmp_path / "test.db"


def _sample_metadata(node: str = "node_extract", model: str | None = None,
                     in_tok: int | None = None, out_tok: int | None = None) -> dict:
    return {
        "node": node,
        "timestamp": 1000.0,
        "latency_ms": 42.5,
        "model": model,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
    }


# ---------------------------------------------------------------------------
# token_cost_eur
# ---------------------------------------------------------------------------

class TestTokenCostEur:
    def test_haiku_calculation(self):
        # 1000 input @ $0.80/M + 500 output @ $4.00/M = $0.0008 + $0.002 = $0.0028 USD
        # × 0.92 EUR = 0.002576
        result = token_cost_eur("claude-haiku-4-5-20251001", 1000, 500)
        assert result == pytest.approx(0.002576, rel=1e-4)

    def test_sonnet_calculation(self):
        result = token_cost_eur("claude-sonnet-4-6", 1000, 500)
        # $0.003 + $0.0075 = $0.0105 × 0.92 = 0.00966
        assert result == pytest.approx(0.00966, rel=1e-4)

    def test_local_model_returns_zero(self):
        assert token_cost_eur("gemma-4-26b", 1000, 500) == 0.0

    def test_none_model_returns_none(self):
        assert token_cost_eur(None, 100, 100) is None

    def test_none_tokens_returns_none(self):
        assert token_cost_eur("claude-haiku-4-5-20251001", None, 100) is None
        assert token_cost_eur("claude-haiku-4-5-20251001", 100, None) is None

    def test_zero_tokens_returns_zero_cost(self):
        assert token_cost_eur("claude-haiku-4-5-20251001", 0, 0) == 0.0

    def test_case_insensitive_model_match(self):
        result = token_cost_eur("CLAUDE-HAIKU-4-5-20251001", 1000, 0)
        assert result is not None and result > 0


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------

class TestInitDb:
    def test_creates_table(self, db):
        init_db(db)
        conn = sqlite3.connect(db)
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        conn.close()
        assert ("node_runs",) in tables

    def test_idempotent(self, db):
        init_db(db)
        init_db(db)  # second call must not raise

    def test_columns_present(self, db):
        init_db(db)
        conn = sqlite3.connect(db)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(node_runs)").fetchall()]
        conn.close()
        for col in ("request_id", "node", "latency_ms", "cost_eur", "compliance_passed"):
            assert col in cols


# ---------------------------------------------------------------------------
# persist_run
# ---------------------------------------------------------------------------

class TestPersistRun:
    def test_inserts_one_row_per_metadata(self, db):
        meta = [_sample_metadata("node_extract"), _sample_metadata("node_client_lookup")]
        persist_run("req-1", meta, db_path=db)
        conn = sqlite3.connect(db)
        count = conn.execute("SELECT COUNT(*) FROM node_runs WHERE request_id='req-1'").fetchone()[0]
        conn.close()
        assert count == 2

    def test_latency_stored_correctly(self, db):
        persist_run("req-2", [_sample_metadata("node_extract")], db_path=db)
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT latency_ms FROM node_runs WHERE request_id='req-2'").fetchone()
        conn.close()
        assert row[0] == pytest.approx(42.5)

    def test_compliance_passed_stored_as_int(self, db):
        persist_run("req-3", [_sample_metadata()], compliance_passed=True, db_path=db)
        conn = sqlite3.connect(db)
        val = conn.execute("SELECT compliance_passed FROM node_runs WHERE request_id='req-3'").fetchone()[0]
        conn.close()
        assert val == 1

    def test_compliance_failed_stored_as_zero(self, db):
        persist_run("req-4", [_sample_metadata()], compliance_passed=False, db_path=db)
        conn = sqlite3.connect(db)
        val = conn.execute("SELECT compliance_passed FROM node_runs WHERE request_id='req-4'").fetchone()[0]
        conn.close()
        assert val == 0

    def test_cost_eur_computed_for_haiku(self, db):
        meta = [_sample_metadata("node_extract", "claude-haiku-4-5-20251001", 400, 200)]
        persist_run("req-5", meta, db_path=db)
        conn = sqlite3.connect(db)
        cost = conn.execute("SELECT cost_eur FROM node_runs WHERE request_id='req-5'").fetchone()[0]
        conn.close()
        assert cost is not None and cost > 0

    def test_cost_eur_null_when_no_tokens(self, db):
        meta = [_sample_metadata("node_extract", model=None)]
        persist_run("req-6", meta, db_path=db)
        conn = sqlite3.connect(db)
        cost = conn.execute("SELECT cost_eur FROM node_runs WHERE request_id='req-6'").fetchone()[0]
        conn.close()
        assert cost is None

    def test_industry_type_stored(self, db):
        persist_run("req-7", [_sample_metadata()], industry_type="Beratung", db_path=db)
        conn = sqlite3.connect(db)
        val = conn.execute("SELECT industry_type FROM node_runs WHERE request_id='req-7'").fetchone()[0]
        conn.close()
        assert val == "Beratung"

    def test_empty_metadata_writes_nothing(self, db):
        persist_run("req-8", [], db_path=db)
        conn = sqlite3.connect(db)
        count = conn.execute("SELECT COUNT(*) FROM node_runs WHERE request_id='req-8'").fetchone()[0]
        conn.close()
        assert count == 0


# ---------------------------------------------------------------------------
# Query functions (populated DB)
# ---------------------------------------------------------------------------

@pytest.fixture
def populated_db(db) -> Path:
    """DB with two requests: one passed compliance, one clarification used."""
    metas_req1 = [
        {"node": "node_extract",          "timestamp": 1.0, "latency_ms": 120.0, "model": "claude-haiku-4-5-20251001", "input_tokens": 400, "output_tokens": 200},
        {"node": "node_client_lookup",    "timestamp": 2.0, "latency_ms":   5.0, "model": None, "input_tokens": None,  "output_tokens": None},
        {"node": "node_check_compliance", "timestamp": 3.0, "latency_ms":   2.0, "model": None, "input_tokens": None,  "output_tokens": None},
        {"node": "node_render_pdf",       "timestamp": 4.0, "latency_ms": 350.0, "model": None, "input_tokens": None,  "output_tokens": None},
    ]
    metas_req2 = [
        {"node": "node_extract",       "timestamp": 5.0, "latency_ms":  90.0, "model": "claude-haiku-4-5-20251001", "input_tokens": 380, "output_tokens": 190},
        {"node": "node_scope_clarify", "timestamp": 6.0, "latency_ms":   1.0, "model": None, "input_tokens": None, "output_tokens": None},
        {"node": "node_check_compliance", "timestamp": 7.0, "latency_ms": 2.5, "model": None, "input_tokens": None, "output_tokens": None},
    ]
    persist_run("req-A", metas_req1, industry_type="Beratung",  compliance_passed=True,  db_path=db)
    persist_run("req-B", metas_req2, industry_type="Handwerk",  compliance_passed=False, db_path=db)
    return db


class TestQueries:
    def test_p95_latency_returns_all_nodes(self, populated_db):
        rows = p95_latency_per_node(populated_db)
        node_names = {r["node"] for r in rows}
        assert "node_extract" in node_names
        assert "node_render_pdf" in node_names

    def test_p95_latency_has_count(self, populated_db):
        rows = p95_latency_per_node(populated_db)
        extract_row = next(r for r in rows if r["node"] == "node_extract")
        assert extract_row["count"] == 2

    def test_avg_cost_per_document_positive(self, populated_db):
        cost = avg_cost_per_document(populated_db)
        assert cost is not None and cost > 0

    def test_compliance_pass_rate(self, populated_db):
        rate = compliance_pass_rate(populated_db)
        assert rate == pytest.approx(0.5)

    def test_clarification_trigger_rate(self, populated_db):
        rate = clarification_trigger_rate(populated_db)
        # req-B triggered scope_clarify → 1 of 2 requests = 0.5
        assert rate == pytest.approx(0.5)

    def test_cost_by_industry(self, populated_db):
        rows = cost_by_industry(populated_db)
        industries = {r["industry"] for r in rows}
        assert "Beratung" in industries
        assert "Handwerk" in industries

    def test_token_usage_by_model(self, populated_db):
        rows = token_usage_by_model(populated_db)
        assert len(rows) >= 1
        haiku_row = next((r for r in rows if "haiku" in r["model"]), None)
        assert haiku_row is not None
        assert haiku_row["total_input"] == 780   # 400 + 380
        assert haiku_row["total_output"] == 390  # 200 + 190

    def test_empty_db_queries_return_none_or_empty(self, db):
        init_db(db)  # table exists but no rows
        assert avg_cost_per_document(db) is None
        assert compliance_pass_rate(db) is None
        assert clarification_trigger_rate(db) is None
        assert p95_latency_per_node(db) == []
        assert cost_by_industry(db) == []
        assert token_usage_by_model(db) == []
