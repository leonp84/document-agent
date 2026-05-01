"""Tests for the FastAPI layer."""
import json
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

_COMPLIANCE_OK = {"passed": True, "failures": []}

_COMPLIANCE_FAIL_DELIVERY = {
    "passed": False,
    "failures": [{"field": "delivery_date", "reason": "missing"}],
}


class _FakeCheckpointerCtx:
    """Sync-compatible async context manager yielding a MemorySaver."""

    def __init__(self, path: str):
        pass

    async def __aenter__(self):
        return MemorySaver()

    async def __aexit__(self, *args):
        pass


def _make_snapshot(interrupt_val: dict | None = None, has_pending_task: bool = False):
    """Return a fake LangGraph state snapshot.

    interrupt_val  — if set, the snapshot carries a clarification interrupt.
    has_pending_task — if True (and no interrupt), simulate a node suspended mid-run
                       (e.g. node_human_review) that appears in snapshot.tasks.
    """
    snapshot = MagicMock()
    if interrupt_val:
        interrupt_item = MagicMock()
        interrupt_item.value = interrupt_val
        task = MagicMock()
        task.interrupts = [interrupt_item]
        snapshot.tasks = [task]
    elif has_pending_task:
        task = MagicMock()
        task.interrupts = []
        snapshot.tasks = [task]
    else:
        snapshot.tasks = []
    return snapshot


@pytest.fixture()
def mock_graph():
    """Happy-path mock: quote generation suspends, approval produces a PDF."""
    graph = MagicMock()

    async def _ainvoke(state_or_cmd, config=None, **kwargs):
        if isinstance(state_or_cmd, Command):
            return {
                "request_id": "test-id",
                "quote": _QUOTE,
                "pdf_bytes": b"fake-pdf",
                "error": None,
                "per_node_metadata": [],
                "compliance_result": _COMPLIANCE_OK,
                "correction_attempts": 0,
            }
        return {
            "request_id": state_or_cmd.get("request_id", "test-id"),
            "quote": _QUOTE,
            "pdf_bytes": None,
            "error": None,
            "per_node_metadata": [],
            "compliance_result": None,
            "correction_attempts": 0,
        }

    graph.ainvoke = AsyncMock(side_effect=_ainvoke)
    # For the human-review case: snapshot has a pending task but no clarification interrupt.
    graph.aget_state = AsyncMock(return_value=_make_snapshot(has_pending_task=True))
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
# POST /invoice  (now async — returns 202, PDF served via GET /pdf/{id})
# ---------------------------------------------------------------------------


def test_post_invoice_unknown_id_returns_404(client):
    r = client.post("/invoice/nonexistent-id", headers=HEADERS)
    assert r.status_code == 404


def test_post_invoice_wrong_status_returns_409(client):
    r = client.post("/quote", json={"raw_input": "test"}, headers=HEADERS)
    request_id = r.json()["request_id"]

    # First approval kicks off background task → job becomes "completed"
    client.post(f"/invoice/{request_id}", headers=HEADERS)

    # Second call on a completed job → 409
    r2 = client.post(f"/invoice/{request_id}", headers=HEADERS)
    assert r2.status_code == 409


def test_post_invoice_returns_202_with_request_id(client):
    r = client.post("/quote", json={"raw_input": "Reinigung Muster GmbH"}, headers=HEADERS)
    request_id = r.json()["request_id"]

    inv_r = client.post(f"/invoice/{request_id}", headers=HEADERS)
    assert inv_r.status_code == 202
    assert inv_r.json()["request_id"] == request_id


def test_post_invoice_background_task_sets_completed(client):
    r = client.post("/quote", json={"raw_input": "test"}, headers=HEADERS)
    request_id = r.json()["request_id"]

    client.post(f"/invoice/{request_id}", headers=HEADERS)

    # Background task runs synchronously in TestClient before the response returns
    status_r = client.get(f"/status/{request_id}", headers=HEADERS)
    assert status_r.json()["status"] == "completed"


# ---------------------------------------------------------------------------
# GET /pdf
# ---------------------------------------------------------------------------


def test_get_pdf_returns_stored_pdf(client):
    r = client.post("/quote", json={"raw_input": "test"}, headers=HEADERS)
    request_id = r.json()["request_id"]
    client.post(f"/invoice/{request_id}", headers=HEADERS)  # background stores pdf_bytes

    pdf_r = client.get(f"/pdf/{request_id}", headers=HEADERS)
    assert pdf_r.status_code == 200
    assert pdf_r.headers["content-type"] == "application/pdf"
    assert pdf_r.content == b"fake-pdf"


def test_get_pdf_sets_content_disposition(client):
    r = client.post("/quote", json={"raw_input": "test"}, headers=HEADERS)
    request_id = r.json()["request_id"]
    client.post(f"/invoice/{request_id}", headers=HEADERS)

    pdf_r = client.get(f"/pdf/{request_id}", headers=HEADERS)
    assert f"invoice-{request_id}.pdf" in pdf_r.headers["content-disposition"]


def test_get_pdf_unknown_id_returns_404(client):
    r = client.get("/pdf/nonexistent-id", headers=HEADERS)
    assert r.status_code == 404


def test_get_pdf_not_completed_returns_409(client):
    # Job created but never approved — still awaiting_approval
    r = client.post("/quote", json={"raw_input": "test"}, headers=HEADERS)
    request_id = r.json()["request_id"]

    pdf_r = client.get(f"/pdf/{request_id}", headers=HEADERS)
    assert pdf_r.status_code == 409


# ---------------------------------------------------------------------------
# Scope clarification unhappy path
# ---------------------------------------------------------------------------


@pytest.fixture()
def scope_clarification_graph(mock_graph):
    """Graph that returns low-confidence scope and suspends at scope_clarify."""
    clarification = {
        "type": "scope_clarification",
        "message": "Too vague — please describe services and client.",
        "original_input": "mach was",
    }

    async def _ainvoke(state_or_cmd, config=None, **kwargs):
        if isinstance(state_or_cmd, Command):
            # After clarification is submitted — produces a quote
            return {
                "request_id": "test-id",
                "quote": _QUOTE,
                "pdf_bytes": None,
                "error": None,
                "per_node_metadata": [],
                "compliance_result": None,
                "correction_attempts": 0,
            }
        # Initial invoke — no quote, suspended at scope_clarify
        return {
            "request_id": state_or_cmd.get("request_id", "test-id"),
            "quote": None,
            "pdf_bytes": None,
            "error": None,
            "per_node_metadata": [],
            "compliance_result": None,
            "correction_attempts": 0,
        }

    mock_graph.ainvoke = AsyncMock(side_effect=_ainvoke)
    # First aget_state call (after initial invoke, scope suspended) → scope clarification.
    # Second call (after clarify resume, quote ready, human_review) → pending task only.
    mock_graph.aget_state = AsyncMock(side_effect=[
        _make_snapshot(interrupt_val=clarification),
        _make_snapshot(has_pending_task=True),
    ])
    return mock_graph


@pytest.fixture()
def client_scope_clarify(tmp_path, scope_clarification_graph, monkeypatch):
    monkeypatch.setenv("DOCASSIST_API_KEY", API_KEY)
    with patch("api.app.AsyncSqliteSaver") as MockSaver, patch(
        "api.app.build_graph", return_value=scope_clarification_graph
    ):
        MockSaver.from_conn_string = _FakeCheckpointerCtx
        with TestClient(create_app(db_path=tmp_path / "scope.db")) as c:
            yield c


def test_scope_clarification_sets_awaiting_clarification_status(client_scope_clarify):
    r = client_scope_clarify.post("/quote", json={"raw_input": "mach was"}, headers=HEADERS)
    request_id = r.json()["request_id"]

    status_r = client_scope_clarify.get(f"/status/{request_id}", headers=HEADERS)
    body = status_r.json()
    assert body["status"] == "awaiting_clarification"
    assert body["clarification"]["type"] == "scope_clarification"
    assert "original_input" in body["clarification"]


def test_scope_clarification_clarify_endpoint_resumes_to_awaiting_approval(client_scope_clarify):
    r = client_scope_clarify.post("/quote", json={"raw_input": "mach was"}, headers=HEADERS)
    request_id = r.json()["request_id"]

    clarify_r = client_scope_clarify.post(
        f"/clarify/{request_id}",
        json={"clarified_input": "Reinigung Muster GmbH 10 Stunden à 25 Euro"},
        headers=HEADERS,
    )
    assert clarify_r.status_code == 200

    status_r = client_scope_clarify.get(f"/status/{request_id}", headers=HEADERS)
    assert status_r.json()["status"] == "awaiting_approval"


def test_clarify_wrong_status_returns_409(client):
    # Job is awaiting_approval, not awaiting_clarification
    r = client.post("/quote", json={"raw_input": "test"}, headers=HEADERS)
    request_id = r.json()["request_id"]

    clarify_r = client.post(
        f"/clarify/{request_id}",
        json={"clarified_input": "something"},
        headers=HEADERS,
    )
    assert clarify_r.status_code == 409


# ---------------------------------------------------------------------------
# Compliance clarification unhappy path
# ---------------------------------------------------------------------------


@pytest.fixture()
def compliance_clarification_graph(mock_graph):
    """Graph that exhausts LLM correction and suspends at compliance_clarify."""
    clarification = {
        "type": "compliance_clarification",
        "message": "Please provide the missing information:",
        "fields": [{"name": "delivery_date", "input_type": "date"}],
    }

    call_count = 0

    async def _ainvoke(state_or_cmd, config=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if isinstance(state_or_cmd, Command) and hasattr(state_or_cmd, "resume"):
            resume = state_or_cmd.resume
            if isinstance(resume, dict) and resume.get("status") == "approved":
                # Approval → compliance fails, no pdf
                return {
                    "request_id": "test-id",
                    "quote": _QUOTE,
                    "pdf_bytes": None,
                    "error": None,
                    "per_node_metadata": [],
                    "compliance_result": _COMPLIANCE_FAIL_DELIVERY,
                    "correction_attempts": 2,
                }
            # Compliance clarification submitted → PDF produced
            return {
                "request_id": "test-id",
                "quote": _QUOTE,
                "pdf_bytes": b"fake-pdf",
                "error": None,
                "per_node_metadata": [],
                "compliance_result": _COMPLIANCE_OK,
                "correction_attempts": 0,
            }
        # Initial invoke → quote ready, suspend at human review
        return {
            "request_id": state_or_cmd.get("request_id", "test-id"),
            "quote": _QUOTE,
            "pdf_bytes": None,
            "error": None,
            "per_node_metadata": [],
            "compliance_result": None,
            "correction_attempts": 0,
        }

    mock_graph.ainvoke = AsyncMock(side_effect=_ainvoke)
    # First aget_state call (after initial quote invoke) → human_review pending task.
    # Second call (after approval invoke) → compliance_clarification interrupt.
    mock_graph.aget_state = AsyncMock(side_effect=[
        _make_snapshot(has_pending_task=True),
        _make_snapshot(interrupt_val=clarification),
        _make_snapshot(has_pending_task=False),  # after clarify submit → completed
    ])
    return mock_graph


@pytest.fixture()
def client_compliance_clarify(tmp_path, compliance_clarification_graph, monkeypatch):
    monkeypatch.setenv("DOCASSIST_API_KEY", API_KEY)
    with patch("api.app.AsyncSqliteSaver") as MockSaver, patch(
        "api.app.build_graph", return_value=compliance_clarification_graph
    ):
        MockSaver.from_conn_string = _FakeCheckpointerCtx
        with TestClient(create_app(db_path=tmp_path / "compliance.db")) as c:
            yield c


def test_compliance_clarification_after_approval_sets_awaiting_clarification(client_compliance_clarify):
    r = client_compliance_clarify.post("/quote", json={"raw_input": "test"}, headers=HEADERS)
    request_id = r.json()["request_id"]

    # Approve → graph exhausts LLM corrections → compliance clarification interrupt
    client_compliance_clarify.post(f"/invoice/{request_id}", headers=HEADERS)

    status_r = client_compliance_clarify.get(f"/status/{request_id}", headers=HEADERS)
    body = status_r.json()
    assert body["status"] == "awaiting_clarification"
    assert body["clarification"]["type"] == "compliance_clarification"
    fields = body["clarification"]["fields"]
    assert any(f["name"] == "delivery_date" for f in fields)


def test_compliance_clarification_submit_produces_pdf(client_compliance_clarify):
    r = client_compliance_clarify.post("/quote", json={"raw_input": "test"}, headers=HEADERS)
    request_id = r.json()["request_id"]

    client_compliance_clarify.post(f"/invoice/{request_id}", headers=HEADERS)

    clarify_r = client_compliance_clarify.post(
        f"/clarify/{request_id}",
        json={"compliance_data": {"delivery_date": "2026-04-28"}},
        headers=HEADERS,
    )
    assert clarify_r.status_code == 200

    pdf_r = client_compliance_clarify.get(f"/pdf/{request_id}", headers=HEADERS)
    assert pdf_r.status_code == 200
    assert pdf_r.content == b"fake-pdf"


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_post_quote_rate_limit(client):
    for _ in range(5):
        r = client.post("/quote", json={"raw_input": "test"}, headers=HEADERS)
        assert r.status_code == 200

    r = client.post("/quote", json={"raw_input": "test"}, headers=HEADERS)
    assert r.status_code == 429
