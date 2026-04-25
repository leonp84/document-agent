"""Unit tests for agent.quote_generator — deterministic parts only, no LLM calls."""
import json
from unittest.mock import patch

import pytest

from agent.models import (
    ClientRecord,
    ResolvedScope,
    ResolvedServiceLine,
    UnresolvedServiceLine,
)
from agent.quote_generator import _assemble_quote, _parse_llm_response, generate_quote

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_CLIENT = ClientRecord(
    id="c1",
    name="Muster GmbH",
    address_line1="Hauptstraße 1",
    address_line2="1010 Wien",
)

_LLM_RESPONSE = json.dumps({
    "line_descriptions": ["ERP Implementierungsberatung – Phase 1"],
    "payment_terms": "Zahlbar innerhalb von 14 Tagen",
})


def _scope(
    *lines: tuple,
    unresolved: list | None = None,
    language: str = "de",
    vat_rate: float = 0.20,
    client: ClientRecord | None = _CLIENT,
) -> ResolvedScope:
    """Build a ResolvedScope from (description, qty, unit, rate) tuples."""
    return ResolvedScope(
        client=client,
        client_ref=client.name if client else "Unknown",
        resolved=[
            ResolvedServiceLine(description=d, quantity=q, unit=u, rate=r)
            for d, q, u, r in lines
        ],
        unresolved=[
            UnresolvedServiceLine(description=d, quantity=q, unit=u)
            for d, q, u in (unresolved or [])
        ],
        vat_rate=vat_rate,
        language=language,
    )


# ---------------------------------------------------------------------------
# _parse_llm_response
# ---------------------------------------------------------------------------

class TestParseLlmResponse:
    def test_valid_json(self):
        raw = json.dumps({"line_descriptions": ["Desc A", "Desc B"], "payment_terms": "14 Tage"})
        descs, terms = _parse_llm_response(raw, 2)
        assert descs == ["Desc A", "Desc B"]
        assert terms == "14 Tage"

    def test_strips_json_code_fence(self):
        raw = "```json\n{\"line_descriptions\": [\"X\"], \"payment_terms\": \"Y\"}\n```"
        descs, terms = _parse_llm_response(raw, 1)
        assert descs == ["X"]

    def test_strips_plain_code_fence(self):
        raw = "```\n{\"line_descriptions\": [\"X\"], \"payment_terms\": \"Y\"}\n```"
        descs, terms = _parse_llm_response(raw, 1)
        assert descs == ["X"]

    def test_wrong_count_raises(self):
        raw = json.dumps({"line_descriptions": ["A", "B"], "payment_terms": "T"})
        with pytest.raises(ValueError):
            _parse_llm_response(raw, 3)

    def test_invalid_json_raises(self):
        with pytest.raises(Exception):
            _parse_llm_response("not json", 1)


# ---------------------------------------------------------------------------
# _assemble_quote
# ---------------------------------------------------------------------------

class TestAssembleQuote:
    def test_amount_is_qty_times_rate(self):
        scope = _scope(("ERP Beratung", 5.0, "Tage", 1100.0))
        quote = _assemble_quote(scope, ["ERP Implementierungsberatung – Phase 1"], "Zahlbar innerhalb von 14 Tagen")
        assert quote.line_items[0].amount == 5500.0

    def test_null_qty_defaults_to_one(self):
        scope = _scope(("Reisekosten", None, "pauschal", 185.0))
        quote = _assemble_quote(scope, ["Reisekosten"], "Zahlbar innerhalb von 14 Tagen")
        assert quote.line_items[0].qty == 1.0
        assert quote.line_items[0].amount == 185.0

    def test_null_unit_defaults_to_pauschal(self):
        scope = _scope(("Sonderleistung", 1.0, None, 300.0))
        quote = _assemble_quote(scope, ["Sonderleistung"], "T")
        assert quote.line_items[0].unit == "pauschal"

    def test_net_total_is_sum_of_amounts(self):
        scope = _scope(
            ("Beratung", 3.0, "Tage", 1000.0),
            ("Workshop", 1.0, "Tage", 1400.0),
        )
        quote = _assemble_quote(scope, ["Beratung", "Workshop"], "T")
        assert quote.net_total == 4400.0

    def test_vat_amount(self):
        scope = _scope(("Beratung", 5.0, "Tage", 1100.0))
        quote = _assemble_quote(scope, ["Beratung"], "T")
        assert quote.vat_amount == 1100.0

    def test_gross_total(self):
        scope = _scope(("Beratung", 5.0, "Tage", 1100.0))
        quote = _assemble_quote(scope, ["Beratung"], "T")
        assert quote.gross_total == 6600.0

    def test_fractional_qty_arithmetic(self):
        # bt_003: 0.5 Tage à 1100 → 550.00
        scope = _scope(("Dokumentation", 0.5, "Tage", 1100.0))
        quote = _assemble_quote(scope, ["Dokumentation und Bericht"], "T")
        assert quote.line_items[0].amount == 550.0

    def test_multi_line_totals_bt003(self):
        scope = _scope(
            ("Strategieworkshop", 1.0, "Tage", 1400.0),
            ("Dokumentation und Bericht", 0.5, "Tage", 1100.0),
            ("Nachbesprechung Geschäftsführung", 0.5, "Tage", 1400.0),
        )
        descs = ["Strategieworkshop", "Dokumentation und Bericht", "Nachbesprechung Geschäftsführung"]
        quote = _assemble_quote(scope, descs, "Zahlbar innerhalb von 14 Tagen")
        assert quote.net_total == 2650.0
        assert quote.vat_amount == 530.0
        assert quote.gross_total == 3180.0

    def test_client_attached(self):
        scope = _scope(("Beratung", 1.0, "Tage", 1000.0))
        quote = _assemble_quote(scope, ["Beratung"], "T")
        assert quote.client is not None
        assert quote.client.id == "c1"

    def test_language_preserved(self):
        scope = _scope(("Business Review", 2.0, "Tage", 950.0), language="en")
        quote = _assemble_quote(scope, ["Business Review"], "Payable within 14 days")
        assert quote.language == "en"

    def test_descriptions_come_from_llm_not_scope(self):
        scope = _scope(("raw input description", 1.0, "Tage", 1000.0))
        quote = _assemble_quote(scope, ["Formatted Description"], "T")
        assert quote.line_items[0].description == "Formatted Description"


# ---------------------------------------------------------------------------
# generate_quote (integration — LLM call mocked)
# ---------------------------------------------------------------------------

class TestGenerateQuote:
    def test_unresolved_lines_returns_none(self):
        scope = _scope(
            ("Beratung", 2.0, "Tage", 1000.0),
            unresolved=[("Sondermaterial", 1, "pauschal")],
        )
        assert generate_quote(scope) is None

    def test_returns_quote_model_on_success(self):
        scope = _scope(("ERP Beratung Phase 1", 5.0, "Tage", 1100.0))
        with patch("agent.quote_generator._call_openai", return_value=_LLM_RESPONSE):
            quote = generate_quote(scope)
        assert quote is not None
        assert quote.net_total == 5500.0
        assert quote.gross_total == 6600.0

    def test_llm_json_error_returns_none(self):
        scope = _scope(("Beratung", 1.0, "Tage", 1000.0))
        with patch("agent.quote_generator._call_openai", return_value="not json"):
            result = generate_quote(scope)
        assert result is None

    def test_llm_exception_returns_none(self):
        scope = _scope(("Beratung", 1.0, "Tage", 1000.0))
        with patch("agent.quote_generator._call_openai", side_effect=RuntimeError("connection refused")):
            result = generate_quote(scope)
        assert result is None

    def test_payment_terms_from_llm(self):
        scope = _scope(("Beratung", 2.0, "Tage", 1000.0))
        response = json.dumps({
            "line_descriptions": ["Beratung"],
            "payment_terms": "Zahlbar innerhalb von 14 Tagen",
        })
        with patch("agent.quote_generator._call_openai", return_value=response):
            quote = generate_quote(scope)
        assert quote.payment_terms == "Zahlbar innerhalb von 14 Tagen"

    def test_anthropic_provider_selected(self, monkeypatch):
        monkeypatch.setenv("DOCASSIST_PROVIDER", "anthropic")
        scope = _scope(("Beratung", 1.0, "Tage", 1000.0))
        response = json.dumps({"line_descriptions": ["Beratung"], "payment_terms": "T"})
        with patch("agent.quote_generator._call_anthropic", return_value=response) as mock:
            generate_quote(scope)
        mock.assert_called_once()

    def test_empty_resolved_list(self):
        # Edge: scope with no service lines and no unresolved — valid but odd
        scope = ResolvedScope(
            client=_CLIENT,
            client_ref="Muster GmbH",
            resolved=[],
            unresolved=[],
            vat_rate=0.20,
            language="de",
        )
        response = json.dumps({"line_descriptions": [], "payment_terms": "Zahlbar innerhalb von 14 Tagen"})
        with patch("agent.quote_generator._call_openai", return_value=response):
            quote = generate_quote(scope)
        assert quote is not None
        assert quote.net_total == 0.0
        assert quote.gross_total == 0.0
