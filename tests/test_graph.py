"""Unit tests for agent.graph — routing logic and correction loop. No live LLM calls."""
import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from langgraph.types import Command

from agent.graph import (
    DocAssistState,
    _apply_correction_patch,
    _build_clarify_fields,
    _next_invoice_number,
    build_graph,
    initial_state,
    route_after_compliance,
    route_after_extract,
    route_after_quote,
    route_after_resolve,
    route_after_review,
)
from agent.models import (
    ClientRecord,
    ComplianceFailure,
    ComplianceResult,
    InvoiceModel,
    QuoteLineItem,
    QuoteModel,
    ResolvedScope,
    ResolvedServiceLine,
    ScopeModel,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_CLIENT = ClientRecord(
    id="c1",
    name="Muster GmbH",
    short_names=[],
    address_line1="Hauptstraße 1",
    address_line2="1010 Wien",
    uid="ATU87654321",
)

_LINE = QuoteLineItem(description="ERP Beratung", qty=5.0, unit="Tage", rate=1100.0, amount=5500.0)

_GOOD_INVOICE = InvoiceModel(
    client=_CLIENT,
    client_ref="Muster GmbH",
    line_items=[_LINE],
    net_total=5500.0,
    vat_rate=0.20,
    vat_amount=1100.0,
    gross_total=6600.0,
    payment_terms="Zahlbar innerhalb von 14 Tagen",
    language="de",
    invoice_number="RE-2025-001",
    invoice_date=date(2025, 6, 1),
    delivery_date=date(2025, 5, 30),
    supplier_name="Test GmbH",
    supplier_address_line1="Testgasse 1",
    supplier_address_line2="1010 Wien",
    supplier_uid="ATU12345678",
    recipient_name="Muster GmbH",
    recipient_address_line1="Hauptstraße 1",
    recipient_address_line2="1010 Wien",
)


def _state(**overrides) -> DocAssistState:
    base = initial_state("Rechnung an Muster GmbH, ERP Beratung 5 Tage")
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# initial_state
# ---------------------------------------------------------------------------

class TestInitialState:
    def test_required_field_set(self):
        s = initial_state("test input")
        assert s["raw_input"] == "test input"

    def test_request_id_is_uuid(self):
        import re
        s = initial_state("test input")
        assert re.match(r"^[0-9a-f-]{36}$", s["request_id"])

    def test_each_call_gets_unique_request_id(self):
        assert initial_state("x")["request_id"] != initial_state("x")["request_id"]

    def test_accumulating_fields_are_empty_lists(self):
        s = initial_state("x")
        assert s["clarifications_needed"] == []
        assert s["per_node_metadata"] == []

    def test_correction_attempts_zero(self):
        assert initial_state("x")["correction_attempts"] == 0


# ---------------------------------------------------------------------------
# route_after_extract
# ---------------------------------------------------------------------------

class TestRouteAfterExtract:
    def test_high_confidence_routes_to_client_lookup(self):
        scope = ScopeModel(client_ref="Muster GmbH", services=[], confidence="high")
        s = _state(scope=scope.model_dump(mode="json"))
        assert route_after_extract(s) == "node_client_lookup"

    def test_low_confidence_routes_to_clarify(self):
        scope = ScopeModel(client_ref="", services=[], confidence="low")
        s = _state(scope=scope.model_dump(mode="json"))
        assert route_after_extract(s) == "node_scope_clarify"


# ---------------------------------------------------------------------------
# route_after_resolve
# ---------------------------------------------------------------------------

class TestRouteAfterResolve:
    def _rs(self, unresolved_count: int) -> dict:
        rs = ResolvedScope(
            client=_CLIENT,
            client_ref="Muster GmbH",
            resolved=[ResolvedServiceLine(description="Beratung", quantity=1.0, unit="Tage", rate=1000.0)],
            unresolved=[],
            vat_rate=0.20,
            language="de",
        )
        if unresolved_count:
            from agent.models import UnresolvedServiceLine
            rs = rs.model_copy(update={
                "unresolved": [
                    UnresolvedServiceLine(description=f"Svc{i}", quantity=1.0, unit="pauschal")
                    for i in range(unresolved_count)
                ]
            })
        return rs.model_dump(mode="json")

    def test_no_unresolved_routes_to_quote(self):
        s = _state(resolved_scope=self._rs(0))
        assert route_after_resolve(s) == "node_generate_quote"

    def test_unresolved_routes_to_rate_clarify(self):
        s = _state(resolved_scope=self._rs(2))
        assert route_after_resolve(s) == "node_rate_clarify"


# ---------------------------------------------------------------------------
# route_after_quote
# ---------------------------------------------------------------------------

class TestRouteAfterQuote:
    def test_quote_present_routes_to_human_review(self):
        q = QuoteModel(
            client=None, client_ref="x", line_items=[_LINE],
            net_total=5500.0, vat_rate=0.20, vat_amount=1100.0, gross_total=6600.0,
            payment_terms="14 Tage", language="de",
        )
        s = _state(quote=q.model_dump(mode="json"))
        assert route_after_quote(s) == "node_human_review"

    def test_none_quote_routes_to_end(self):
        from langgraph.graph import END
        s = _state(quote=None)
        assert route_after_quote(s) == END


# ---------------------------------------------------------------------------
# route_after_review
# ---------------------------------------------------------------------------

class TestRouteAfterReview:
    def test_approved_routes_to_build_invoice(self):
        s = _state(approval_status="approved")
        assert route_after_review(s) == "node_build_invoice"

    def test_rejected_routes_to_generate_quote(self):
        s = _state(approval_status="rejected")
        assert route_after_review(s) == "node_generate_quote"

    def test_pending_routes_to_generate_quote(self):
        s = _state(approval_status="pending")
        assert route_after_review(s) == "node_generate_quote"


# ---------------------------------------------------------------------------
# route_after_compliance
# ---------------------------------------------------------------------------

class TestRouteAfterCompliance:
    def _result(self, passed: bool, failures: list | None = None) -> dict:
        return ComplianceResult(
            passed=passed,
            failures=failures or [],
        ).model_dump(mode="json")

    def test_passed_routes_to_render(self):
        s = _state(compliance_result=self._result(True), correction_attempts=0)
        assert route_after_compliance(s) == "node_render_pdf"

    def test_failed_first_attempt_routes_to_correction(self):
        failures = [ComplianceFailure(field="delivery_date", reason="missing")]
        s = _state(compliance_result=self._result(False, failures), correction_attempts=0)
        assert route_after_compliance(s) == "node_correct_compliance"

    def test_failed_second_attempt_routes_to_correction(self):
        failures = [ComplianceFailure(field="delivery_date", reason="missing")]
        s = _state(compliance_result=self._result(False, failures), correction_attempts=1)
        assert route_after_compliance(s) == "node_correct_compliance"

    def test_failed_after_two_attempts_clarifiable_routes_to_compliance_clarify(self):
        # delivery_date is user-clarifiable → compliance clarification interrupt
        failures = [ComplianceFailure(field="delivery_date", reason="missing")]
        s = _state(compliance_result=self._result(False, failures), correction_attempts=2)
        assert route_after_compliance(s) == "node_compliance_clarify"

    def test_failed_after_two_attempts_non_clarifiable_routes_to_end(self):
        # vat_amount mismatch is a system-level error the user cannot fix
        from langgraph.graph import END
        failures = [ComplianceFailure(field="vat_amount", reason="arithmetic mismatch")]
        s = _state(compliance_result=self._result(False, failures), correction_attempts=2)
        assert route_after_compliance(s) == END

    def test_mixed_failures_with_one_clarifiable_routes_to_compliance_clarify(self):
        # If ANY failure is clarifiable we offer the interrupt rather than giving up
        failures = [
            ComplianceFailure(field="vat_amount", reason="arithmetic mismatch"),
            ComplianceFailure(field="delivery_date", reason="missing"),
        ]
        s = _state(compliance_result=self._result(False, failures), correction_attempts=2)
        assert route_after_compliance(s) == "node_compliance_clarify"


# ---------------------------------------------------------------------------
# _build_clarify_fields
# ---------------------------------------------------------------------------

class TestBuildClarifyFields:
    def _result(self, *field_names: str) -> ComplianceResult:
        return ComplianceResult(
            passed=False,
            failures=[ComplianceFailure(field=f, reason="test") for f in field_names],
        )

    def test_delivery_date_produces_date_input(self):
        fields = _build_clarify_fields(self._result("delivery_date"))
        assert len(fields) == 1
        assert fields[0]["name"] == "delivery_date"
        assert fields[0]["input_type"] == "date"

    def test_recipient_uid_produces_text_input_with_placeholder(self):
        fields = _build_clarify_fields(self._result("recipient_uid"))
        assert len(fields) == 1
        assert fields[0]["name"] == "recipient_uid"
        assert fields[0]["input_type"] == "text"
        assert "ATU" in fields[0].get("placeholder", "")

    def test_recipient_name_address_expands_to_three_fields(self):
        fields = _build_clarify_fields(self._result("recipient_name_address"))
        names = [f["name"] for f in fields]
        assert "recipient_name" in names
        assert "recipient_address_line1" in names
        assert "recipient_address_line2" in names

    def test_non_clarifiable_failure_produces_no_fields(self):
        # vat_amount, net_total, line_items etc. are system errors the user can't fix
        assert _build_clarify_fields(self._result("vat_amount")) == []
        assert _build_clarify_fields(self._result("invoice_number")) == []

    def test_duplicate_failures_deduplicated(self):
        # Two failures on the same field → only one input spec
        result = ComplianceResult(
            passed=False,
            failures=[
                ComplianceFailure(field="delivery_date", reason="a"),
                ComplianceFailure(field="delivery_date", reason="b"),
            ],
        )
        assert len(_build_clarify_fields(result)) == 1

    def test_mixed_failures_only_clarifiable_fields_returned(self):
        fields = _build_clarify_fields(self._result("delivery_date", "vat_amount", "recipient_uid"))
        names = [f["name"] for f in fields]
        assert "delivery_date" in names
        assert "recipient_uid" in names
        assert len(fields) == 2  # vat_amount excluded


# ---------------------------------------------------------------------------
# _apply_correction_patch
# ---------------------------------------------------------------------------

class TestApplyCorrectionPatch:
    def test_delivery_date_applied(self):
        patch = {"delivery_date": "2025-03-15", "service_period_from": None,
                 "service_period_to": None, "recipient_name": None,
                 "recipient_address_line1": None, "recipient_address_line2": None,
                 "recipient_uid": None}
        result = _apply_correction_patch(_GOOD_INVOICE.model_copy(update={"delivery_date": None}), patch)
        assert result.delivery_date == date(2025, 3, 15)

    def test_service_period_applied(self):
        patch = {"delivery_date": None,
                 "service_period_from": "2025-05-01", "service_period_to": "2025-05-31",
                 "recipient_name": None, "recipient_address_line1": None,
                 "recipient_address_line2": None, "recipient_uid": None}
        result = _apply_correction_patch(_GOOD_INVOICE.model_copy(update={"delivery_date": None}), patch)
        assert result.service_period_from == date(2025, 5, 1)
        assert result.service_period_to == date(2025, 5, 31)

    def test_recipient_fields_applied(self):
        patch = {"delivery_date": None, "service_period_from": None, "service_period_to": None,
                 "recipient_name": "Bauer OG", "recipient_address_line1": "Wollzeile 8",
                 "recipient_address_line2": "1010 Wien", "recipient_uid": None}
        inv = _GOOD_INVOICE.model_copy(update={"recipient_name": "", "recipient_address_line1": "", "recipient_address_line2": ""})
        result = _apply_correction_patch(inv, patch)
        assert result.recipient_name == "Bauer OG"
        assert result.recipient_address_line1 == "Wollzeile 8"

    def test_all_null_patch_returns_same_invoice(self):
        patch = {k: None for k in (
            "delivery_date", "service_period_from", "service_period_to",
            "recipient_name", "recipient_address_line1", "recipient_address_line2", "recipient_uid",
        )}
        result = _apply_correction_patch(_GOOD_INVOICE, patch)
        assert result == _GOOD_INVOICE

    def test_invalid_date_string_ignored(self):
        patch = {"delivery_date": "not-a-date", "service_period_from": None,
                 "service_period_to": None, "recipient_name": None,
                 "recipient_address_line1": None, "recipient_address_line2": None,
                 "recipient_uid": None}
        result = _apply_correction_patch(_GOOD_INVOICE, patch)
        assert result.delivery_date == _GOOD_INVOICE.delivery_date  # unchanged

    def test_recipient_uid_applied(self):
        patch = {"delivery_date": None, "service_period_from": None, "service_period_to": None,
                 "recipient_name": None, "recipient_address_line1": None,
                 "recipient_address_line2": None, "recipient_uid": "ATU99999999"}
        result = _apply_correction_patch(_GOOD_INVOICE, patch)
        assert result.recipient_uid == "ATU99999999"


# ---------------------------------------------------------------------------
# End-to-end happy path (all LLM calls mocked)
# ---------------------------------------------------------------------------

class TestGraphHappyPath:
    """Full graph run from raw_input to pdf_bytes stub, all LLM calls mocked."""

    def _quote_response(self) -> str:
        return json.dumps({
            "line_descriptions": ["ERP Implementierungsberatung – Phase 1"],
            "payment_terms": "Zahlbar innerhalb von 14 Tagen",
        })

    def _scope_response(self) -> str:
        return json.dumps({
            "client_ref": "Muster GmbH",
            "services": [{"description": "ERP Beratung", "quantity": 5.0, "unit": "Tage", "rate": 1100.0}],
            "vat_rate": 0.20,
            "language": "de",
            "confidence": "high",
        })

    @patch("agent.graph._apply_node_env")
    @patch("agent.graph._get_profile")
    @patch("agent.graph._get_clients")
    @patch("agent.extractor._extract_via_anthropic")
    @patch("agent.quote_generator._call_anthropic")
    def test_happy_path_reaches_human_review(
        self, mock_quote_llm, mock_extract_llm, mock_clients, mock_profile, _mock_env
    ):
        mock_extract_llm.return_value = self._scope_response()
        mock_quote_llm.return_value = self._quote_response()
        mock_profile.return_value = _make_profile()
        mock_clients.return_value = [_CLIENT]

        g = build_graph()
        config = {"configurable": {"thread_id": "test-happy-1"}}

        # First invoke — should suspend at human review interrupt
        result = g.invoke(initial_state("ERP Beratung Muster GmbH 5 Tage"), config=config)

        # LangGraph returns state at interrupt point
        assert result is not None

    @patch("agent.graph._apply_node_env")
    @patch("agent.graph._get_profile")
    @patch("agent.graph._get_clients")
    @patch("agent.graph.extract_scope")
    @patch("agent.graph.generate_quote")
    @patch("agent.graph._call_correction_llm")
    @patch("agent.graph.render_pdf")
    def test_approval_reaches_compliance_check(
        self, mock_render, mock_correction_llm, mock_quote, mock_extract, mock_clients, mock_profile, _mock_env
    ):
        from agent.models import ScopeModel, QuoteModel

        mock_extract.return_value = (
            ScopeModel(
                client_ref="Muster GmbH",
                services=[],
                confidence="high",
                language="de",
            ),
            100, 50,
        )
        # Quote generator returns a valid QuoteModel (no rejection feedback needed)
        q = QuoteModel(
            client=_CLIENT,
            client_ref="Muster GmbH",
            line_items=[_LINE],
            net_total=5500.0, vat_rate=0.20, vat_amount=1100.0, gross_total=6600.0,
            payment_terms="14 Tage", language="de",
        )
        mock_quote.return_value = (q, 200, 100)
        # Correction LLM returns a delivery_date patch so compliance passes on second try
        mock_correction_llm.return_value = (
            json.dumps({"delivery_date": "2025-05-30", "service_period_from": None,
                        "service_period_to": None, "recipient_name": None,
                        "recipient_address_line1": None, "recipient_address_line2": None,
                        "recipient_uid": None}),
            None, None,
        )
        mock_render.return_value = b"fake-pdf"
        mock_profile.return_value = _make_profile()
        mock_clients.return_value = [_CLIENT]

        g = build_graph()
        config = {"configurable": {"thread_id": "test-happy-2"}}

        g.invoke(initial_state("ERP Beratung Muster GmbH 5 Tage"), config=config)

        final = g.invoke(
            Command(resume={"status": "approved", "feedback": None}),
            config=config,
        )
        assert final is not None
        assert final.get("compliance_result") is not None


# ---------------------------------------------------------------------------
# Compliance correction loop integration
# ---------------------------------------------------------------------------

class TestCorrectionLoop:
    """Verify correction loop runs at most twice and then terminates."""

    def test_correction_loop_exhausts_to_compliance_clarify_for_user_fixable_failures(self):
        # delivery_date is clarifiable — loop hands off to the interrupt node, not END
        failing_result = ComplianceResult(
            passed=False,
            failures=[ComplianceFailure(field="delivery_date", reason="missing")],
        )
        s = {
            **initial_state("Rechnung Muster GmbH"),
            "compliance_result": failing_result.model_dump(mode="json"),
            "correction_attempts": 2,
        }
        assert route_after_compliance(s) == "node_compliance_clarify"

    def test_correction_loop_increments_counter(self):
        # delivery_date: 0 → correct, 1 → correct, 2 → compliance_clarify (clarifiable)
        failing = ComplianceResult(
            passed=False,
            failures=[ComplianceFailure(field="delivery_date", reason="missing")],
        ).model_dump(mode="json")

        for attempts, expected in [
            (0, "node_correct_compliance"),
            (1, "node_correct_compliance"),
            (2, "node_compliance_clarify"),
        ]:
            s = {**initial_state("x"), "compliance_result": failing, "correction_attempts": attempts}
            assert route_after_compliance(s) == expected


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_profile():
    from agent.models import BusinessProfile, DefaultRates
    return BusinessProfile(
        name="Test GmbH",
        address_line1="Testgasse 1",
        address_line2="1010 Wien",
        uid="ATU12345678",
        bank_iban="AT12 3456 7890 1234 5678",
        bank_bic="TESTBIC1",
        industry="Beratung",
        language="de",
        default_rates=DefaultRates(labor_hourly=90.0, labor_daily=700.0, material_markup_pct=15.0),
    )
