"""Tests for the FastAPI layer (Phase 10)."""
import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from api.app import create_app

API_KEY = "test-key"
HEADERS = {"X-API-Key": API_KEY}

_QUOTE = {
    "client_ref": "Test GmbH",
    "client": None,
    "line_items": [],
    "net_total": 1000.0,
    "vat_rate": 0.20,
    "vat_amount": 200.0,
    "gross_total": 1200.0,
    "payment_terms": "14 Tage",
    "language": "de",
}


class _FakeCheckpointerCtx:
    """Sync-compatible async context manager yielding a MemorySaver."""

    def __init__(self, path: str):
        pass

    async def __aenter__(self):
        return MemorySaver()

    async def __aexit__(self, *args):
        pass


@pytest.fixture()
def mock_graph():
    graph = MagicMock()

    async def _ainvoke(state_or_cmd, config=None, **kwargs):
        if isinstance(state_or_cmd, Command):
            return {
                "request_id": "test-id",
                "quote": _QUOTE,
                "pdf_bytes": b"fake-pdf",
                "error": None,
                "per_node_metadata": [],
                "correction_attempts": 0,
            }
        return {
            "request_id": state_or_cmd.get("request_id", "test-id"),
            "quote": _QUOTE,
            "pdf_bytes": None,
            "error": None,
            "per_node_metadata": [],
            "correction_attempts": 0,
        }

    graph.ainvoke = AsyncMock(side_effect=_ainvoke)
    return graph


@pytest.fixture()
def app(tmp_path, mock_graph):
    with patch("api.app.AsyncSqliteSaver") as MockSaver, patch(
        "api.app.build_graph", return_value=mock_graph
    ):
        MockSaver.from_conn_string = _FakeCheckpointerCtx
        yield create_app(db_path=tmp_path / "test.db")


@pytest.fixture()
def client(app, monkeypatch):
    monkeypatch.setenv("DOCASSIST_API_KEY", API_KEY)
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Health — no auth required
# ---------------------------------------------------------------------------


def test_health_no_auth(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# API key middleware
# ---------------------------------------------------------------------------


def test_post_quote_no_api_key_returns_401(client):
    r = client.post("/quote", json={"raw_input": "test"})
    assert r.status_code == 401


def test_post_quote_wrong_api_key_returns_401(client):
    r = client.post("/quote", json={"raw_input": "test"}, headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /quote
# ---------------------------------------------------------------------------


def test_post_quote_returns_request_id(client):
    r = client.post("/quote", json={"raw_input": "Reinigung Muster GmbH"}, headers=HEADERS)
    assert r.status_code == 200
    data = r.json()
    assert "request_id" in data
    assert len(data["request_id"]) == 36  # UUID4


def test_post_quote_background_updates_status_to_awaiting_approval(client):
    r = client.post("/quote", json={"raw_input": "Reinigung Muster GmbH"}, headers=HEADERS)
    request_id = r.json()["request_id"]

    # Background task runs before TestClient returns — status should be updated
    status_r = client.get(f"/status/{request_id}", headers=HEADERS)
    assert status_r.status_code == 200
    body = status_r.json()
    assert body["status"] == "awaiting_approval"
    assert body["quote"] is not None
    assert body["quote"]["gross_total"] == 1200.0


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------


def test_get_status_unknown_id_returns_404(client):
    r = client.get("/status/nonexistent-id", headers=HEADERS)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /invoice
# ---------------------------------------------------------------------------


def test_post_invoice_wrong_status_returns_409(client):
    # Create a quote first (puts job in awaiting_approval) then check a
    # second approval attempt returns 409 (already completed after first)
    r = client.post("/quote", json={"raw_input": "test"}, headers=HEADERS)
    request_id = r.json()["request_id"]

    # Approve once → completed
    client.post(f"/invoice/{request_id}", headers=HEADERS)

    # Second approval on a completed job → 409
    r2 = client.post(f"/invoice/{request_id}", headers=HEADERS)
    assert r2.status_code == 409


def test_post_invoice_unknown_id_returns_404(client):
    r = client.post("/invoice/nonexistent-id", headers=HEADERS)
    assert r.status_code == 404


def test_post_invoice_returns_pdf(client):
    # Get quote first
    r = client.post("/quote", json={"raw_input": "Reinigung Muster GmbH"}, headers=HEADERS)
    request_id = r.json()["request_id"]

    # Approve
    inv_r = client.post(f"/invoice/{request_id}", headers=HEADERS)
    assert inv_r.status_code == 200
    assert inv_r.headers["content-type"] == "application/pdf"
    assert inv_r.content == b"fake-pdf"


def test_post_invoice_sets_content_disposition(client):
    r = client.post("/quote", json={"raw_input": "test"}, headers=HEADERS)
    request_id = r.json()["request_id"]

    inv_r = client.post(f"/invoice/{request_id}", headers=HEADERS)
    assert f"invoice-{request_id}.pdf" in inv_r.headers["content-disposition"]


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_post_quote_rate_limit(client):
    for _ in range(5):
        r = client.post("/quote", json={"raw_input": "test"}, headers=HEADERS)
        assert r.status_code == 200

    r = client.post("/quote", json={"raw_input": "test"}, headers=HEADERS)
    assert r.status_code == 429
