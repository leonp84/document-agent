"""Unit tests for agent.compliance_engine — all 11 §11 UStG fields."""
from datetime import date

import pytest

from agent.models import ClientRecord, InvoiceModel, QuoteLineItem
from agent.compliance_engine import compliance_check

# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

_TODAY = date(2025, 6, 1)
_DELIVERY = date(2025, 5, 30)

_LINE = QuoteLineItem(description="ERP Beratung", qty=5.0, unit="Tage", rate=1100.0, amount=5500.0)


def _good_invoice(**overrides) -> InvoiceModel:
    """Return a known-good invoice that passes all 11 §11 UStG checks."""
    defaults = dict(
        client=None,
        client_ref="Muster GmbH",
        line_items=[_LINE],
        net_total=5500.0,
        vat_rate=0.20,
        vat_amount=1100.0,
        gross_total=6600.0,
        payment_terms="Zahlbar innerhalb von 14 Tagen",
        language="de",
        invoice_number="2025-001",
        invoice_date=_TODAY,
        delivery_date=_DELIVERY,
        supplier_name="Test Consulting GmbH",
        supplier_address_line1="Testgasse 1",
        supplier_address_line2="1010 Wien",
        supplier_uid="ATU12345678",
        recipient_name="Muster GmbH",
        recipient_address_line1="Hauptstraße 5",
        recipient_address_line2="1030 Wien",
        recipient_uid=None,
    )
    defaults.update(overrides)
    return InvoiceModel(**defaults)


# ---------------------------------------------------------------------------
# Known-good invoice passes
# ---------------------------------------------------------------------------

class TestKnownGoodPasses:
    def test_good_invoice_passes(self):
        result = compliance_check(_good_invoice())
        assert result.passed is True
        assert result.failures == []

    def test_delivery_date_variant_passes(self):
        inv = _good_invoice(delivery_date=_DELIVERY, service_period_from=None, service_period_to=None)
        assert compliance_check(inv).passed is True

    def test_service_period_variant_passes(self):
        inv = _good_invoice(
            delivery_date=None,
            service_period_from=date(2025, 5, 1),
            service_period_to=date(2025, 5, 31),
        )
        assert compliance_check(inv).passed is True

    def test_recipient_uid_not_required_below_threshold(self):
        # gross_total = 6600, below €10,000 — recipient_uid may be None
        inv = _good_invoice(recipient_uid=None)
        assert compliance_check(inv).passed is True

    def test_recipient_uid_present_above_threshold_passes(self):
        inv = _good_invoice(net_total=9000.0, vat_amount=1800.0, gross_total=10_800.0, recipient_uid="ATU87654321")
        assert compliance_check(inv).passed is True


# ---------------------------------------------------------------------------
# §11 Abs. 1 Z 1 — supplier name and address
# ---------------------------------------------------------------------------

class TestField1SupplierNameAddress:
    def test_empty_supplier_name_fails(self):
        result = compliance_check(_good_invoice(supplier_name=""))
        assert not result.passed
        assert any(f.field == "supplier_name_address" for f in result.failures)

    def test_empty_supplier_address_line1_fails(self):
        result = compliance_check(_good_invoice(supplier_address_line1=""))
        assert not result.passed
        assert any(f.field == "supplier_name_address" for f in result.failures)

    def test_empty_supplier_address_line2_fails(self):
        result = compliance_check(_good_invoice(supplier_address_line2=""))
        assert not result.passed
        assert any(f.field == "supplier_name_address" for f in result.failures)


# ---------------------------------------------------------------------------
# §11 Abs. 1 Z 2 — recipient name and address
# ---------------------------------------------------------------------------

class TestField2RecipientNameAddress:
    def test_empty_recipient_name_fails(self):
        result = compliance_check(_good_invoice(recipient_name=""))
        assert not result.passed
        assert any(f.field == "recipient_name_address" for f in result.failures)

    def test_empty_recipient_address_line1_fails(self):
        result = compliance_check(_good_invoice(recipient_address_line1=""))
        assert not result.passed
        assert any(f.field == "recipient_name_address" for f in result.failures)

    def test_empty_recipient_address_line2_fails(self):
        result = compliance_check(_good_invoice(recipient_address_line2=""))
        assert not result.passed
        assert any(f.field == "recipient_name_address" for f in result.failures)


# ---------------------------------------------------------------------------
# §11 Abs. 1 Z 3 — supplier UID (ATU + 8 digits)
# ---------------------------------------------------------------------------

class TestField3SupplierUid:
    @pytest.mark.parametrize("uid", [
        "ATU12345678",   # valid
        "ATU00000000",   # all zeros — still valid format
    ])
    def test_valid_uid_passes(self, uid):
        assert compliance_check(_good_invoice(supplier_uid=uid)).passed is True

    @pytest.mark.parametrize("uid,desc", [
        ("", "empty"),
        ("ATU1234567", "7 digits"),
        ("ATU123456789", "9 digits"),
        ("atu12345678", "lowercase"),
        ("DE12345678", "wrong prefix"),
        ("ATU1234567X", "non-digit suffix"),
        ("12345678", "no prefix"),
    ])
    def test_invalid_uid_fails(self, uid, desc):
        result = compliance_check(_good_invoice(supplier_uid=uid))
        assert not result.passed, f"Expected failure for UID '{uid}' ({desc})"
        assert any(f.field == "supplier_uid" for f in result.failures)


# ---------------------------------------------------------------------------
# §11 Abs. 1 Z 4 — recipient UID when gross > €10,000
# ---------------------------------------------------------------------------

class TestField4RecipientUid:
    def _above_threshold(self, **overrides):
        base = dict(net_total=9000.0, vat_amount=1800.0, gross_total=10_800.0)
        base.update(overrides)
        return _good_invoice(**base)

    def test_missing_recipient_uid_above_threshold_fails(self):
        result = compliance_check(self._above_threshold(recipient_uid=None))
        assert not result.passed
        assert any(f.field == "recipient_uid" for f in result.failures)

    def test_invalid_uid_format_above_threshold_fails(self):
        result = compliance_check(self._above_threshold(recipient_uid="ATU1234567"))  # 7 digits
        assert not result.passed
        assert any(f.field == "recipient_uid" for f in result.failures)

    def test_valid_uid_above_threshold_passes(self):
        result = compliance_check(self._above_threshold(recipient_uid="ATU87654321"))
        assert result.passed is True

    def test_exactly_at_threshold_not_required(self):
        # gross_total == €10,000 → rule only triggers above €10,000
        inv = _good_invoice(net_total=8333.33, vat_amount=1666.67, gross_total=10_000.0, recipient_uid=None)
        assert compliance_check(inv).passed is True


# ---------------------------------------------------------------------------
# §11 Abs. 1 Z 5 — sequential invoice number
# ---------------------------------------------------------------------------

class TestField5InvoiceNumber:
    def test_empty_invoice_number_fails(self):
        result = compliance_check(_good_invoice(invoice_number=""))
        assert not result.passed
        assert any(f.field == "invoice_number" for f in result.failures)

    def test_non_empty_invoice_number_passes(self):
        assert compliance_check(_good_invoice(invoice_number="RE-2025-042")).passed is True


# ---------------------------------------------------------------------------
# §11 Abs. 1 Z 6 — invoice date (guaranteed by model construction)
# ---------------------------------------------------------------------------

class TestField6InvoiceDate:
    def test_invoice_date_present_passes(self):
        # invoice_date is a required field; Pydantic prevents None at construction time
        assert compliance_check(_good_invoice(invoice_date=date(2025, 1, 1))).passed is True


# ---------------------------------------------------------------------------
# §11 Abs. 1 Z 7 — delivery date or service period
# ---------------------------------------------------------------------------

class TestField7DeliveryOrPeriod:
    def test_neither_delivery_nor_period_fails(self):
        result = compliance_check(_good_invoice(
            delivery_date=None,
            service_period_from=None,
            service_period_to=None,
        ))
        assert not result.passed
        assert any(f.field == "delivery_date" for f in result.failures)

    def test_delivery_date_only_passes(self):
        inv = _good_invoice(delivery_date=_DELIVERY, service_period_from=None, service_period_to=None)
        assert compliance_check(inv).passed is True

    def test_service_period_only_passes(self):
        inv = _good_invoice(
            delivery_date=None,
            service_period_from=date(2025, 5, 1),
            service_period_to=date(2025, 5, 31),
        )
        assert compliance_check(inv).passed is True

    def test_partial_period_only_from_fails(self):
        # from set but to missing → no complete period and no delivery_date
        result = compliance_check(_good_invoice(
            delivery_date=None,
            service_period_from=date(2025, 5, 1),
            service_period_to=None,
        ))
        assert not result.passed
        assert any(f.field == "delivery_date" for f in result.failures)

    def test_partial_period_only_to_fails(self):
        result = compliance_check(_good_invoice(
            delivery_date=None,
            service_period_from=None,
            service_period_to=date(2025, 5, 31),
        ))
        assert not result.passed
        assert any(f.field == "delivery_date" for f in result.failures)


# ---------------------------------------------------------------------------
# §11 Abs. 1 Z 8 — quantity and description of goods/services
# ---------------------------------------------------------------------------

class TestField8LineItems:
    def test_empty_line_items_fails(self):
        result = compliance_check(_good_invoice(line_items=[]))
        assert not result.passed
        assert any(f.field == "line_items" for f in result.failures)

    def test_line_item_missing_description_fails(self):
        bad_line = QuoteLineItem(description="", qty=1.0, unit="Tage", rate=500.0, amount=500.0)
        result = compliance_check(_good_invoice(line_items=[bad_line], net_total=500.0, vat_amount=100.0, gross_total=600.0))
        assert not result.passed
        assert any(f.field == "line_items" for f in result.failures)

    def test_line_item_zero_qty_fails(self):
        bad_line = QuoteLineItem(description="Beratung", qty=0.0, unit="Tage", rate=1000.0, amount=0.0)
        result = compliance_check(_good_invoice(line_items=[bad_line], net_total=0.0, vat_amount=0.0, gross_total=0.0))
        assert not result.passed
        assert any(f.field == "line_items" for f in result.failures)


# ---------------------------------------------------------------------------
# §11 Abs. 1 Z 9 — net amount
# ---------------------------------------------------------------------------

class TestField9NetAmount:
    def test_negative_net_total_fails(self):
        result = compliance_check(_good_invoice(net_total=-100.0, vat_amount=-20.0, gross_total=-120.0))
        assert not result.passed
        assert any(f.field == "net_total" for f in result.failures)

    def test_zero_net_total_passes(self):
        # Zero-value invoice (e.g. internal correction) is not inherently non-compliant
        inv = _good_invoice(net_total=0.0, vat_amount=0.0, gross_total=0.0)
        assert compliance_check(inv).passed is True


# ---------------------------------------------------------------------------
# §11 Abs. 1 Z 10 — VAT rate
# ---------------------------------------------------------------------------

class TestField10VatRate:
    def test_negative_vat_rate_fails(self):
        result = compliance_check(_good_invoice(vat_rate=-0.20))
        assert not result.passed
        assert any(f.field == "vat_rate" for f in result.failures)

    def test_zero_vat_rate_passes(self):
        # Zero-rated (reverse charge) invoices are legal
        inv = _good_invoice(vat_rate=0.0, vat_amount=0.0)
        assert compliance_check(inv).passed is True


# ---------------------------------------------------------------------------
# §11 Abs. 1 Z 11 — VAT amount arithmetic
# ---------------------------------------------------------------------------

class TestField11VatAmount:
    def test_inconsistent_vat_amount_fails(self):
        # net=5500, rate=0.20 → expected vat=1100; supply 999 instead
        result = compliance_check(_good_invoice(vat_amount=999.0))
        assert not result.passed
        assert any(f.field == "vat_amount" for f in result.failures)

    def test_within_rounding_tolerance_passes(self):
        # Allow up to €0.01 rounding difference
        inv = _good_invoice(net_total=5500.0, vat_rate=0.20, vat_amount=1100.00)
        assert compliance_check(inv).passed is True

    def test_failure_reason_contains_amounts(self):
        result = compliance_check(_good_invoice(vat_amount=999.0))
        failure = next(f for f in result.failures if f.field == "vat_amount")
        assert "999" in failure.reason
        assert "1100" in failure.reason


# ---------------------------------------------------------------------------
# ComplianceResult structure
# ---------------------------------------------------------------------------

class TestComplianceResultStructure:
    def test_multiple_failures_all_reported(self):
        inv = _good_invoice(supplier_name="", invoice_number="", delivery_date=None)
        result = compliance_check(inv)
        assert not result.passed
        fields = {f.field for f in result.failures}
        assert "supplier_name_address" in fields
        assert "invoice_number" in fields
        assert "delivery_date" in fields

    def test_failure_has_reason(self):
        result = compliance_check(_good_invoice(invoice_number=""))
        failure = next(f for f in result.failures if f.field == "invoice_number")
        assert "§11" in failure.reason
        assert len(failure.reason) > 10
