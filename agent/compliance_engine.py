"""§11 UStG compliance rules engine. Deterministic — no LLM."""
import re

from agent.models import ComplianceFailure, ComplianceResult, InvoiceModel

_UID_RE = re.compile(r"^ATU\d{8}$")

_RECIPIENT_UID_THRESHOLD = 10_000.0


def compliance_check(invoice: InvoiceModel) -> ComplianceResult:
    """Check all 11 §11 UStG required fields. Returns named failures for each gap."""
    failures: list[ComplianceFailure] = []

    # §11 Abs. 1 Z 1 — supplier name and address (ebInterface: Biller/Address)
    if not (invoice.supplier_name and invoice.supplier_address_line1 and invoice.supplier_address_line2):
        failures.append(ComplianceFailure(
            field="supplier_name_address",
            reason="Supplier name or address is missing (§11 Abs. 1 Z 1 UStG)",
        ))

    # §11 Abs. 1 Z 2 — recipient name and address (ebInterface: InvoiceRecipient/Address)
    if not (invoice.recipient_name and invoice.recipient_address_line1 and invoice.recipient_address_line2):
        failures.append(ComplianceFailure(
            field="recipient_name_address",
            reason="Recipient name or address is missing (§11 Abs. 1 Z 2 UStG)",
        ))

    # §11 Abs. 1 Z 3 — supplier UID in ATU+8-digit format (ebInterface: Biller/VATIdentificationNumber)
    if not _UID_RE.match(invoice.supplier_uid):
        failures.append(ComplianceFailure(
            field="supplier_uid",
            reason="Supplier UID is missing or not in ATU+8-digit format (§11 Abs. 1 Z 3 UStG)",
        ))

    # §11 Abs. 1 Z 4 — recipient UID required when gross total exceeds €10,000
    #                   (ebInterface: InvoiceRecipient/VATIdentificationNumber)
    if invoice.gross_total > _RECIPIENT_UID_THRESHOLD and not _UID_RE.match(invoice.recipient_uid or ""):
        failures.append(ComplianceFailure(
            field="recipient_uid",
            reason=(
                f"Recipient UID required for invoices over €{_RECIPIENT_UID_THRESHOLD:,.0f} "
                "(§11 Abs. 1 Z 4 UStG)"
            ),
        ))

    # §11 Abs. 1 Z 5 — sequential invoice number (ebInterface: InvoiceNumber)
    if not invoice.invoice_number:
        failures.append(ComplianceFailure(
            field="invoice_number",
            reason="Invoice number is missing (§11 Abs. 1 Z 5 UStG)",
        ))

    # §11 Abs. 1 Z 6 — invoice date (ebInterface: InvoiceDate)
    # Always satisfied by model construction (required date field), checked for completeness.
    if invoice.invoice_date is None:
        failures.append(ComplianceFailure(
            field="invoice_date",
            reason="Invoice date is missing (§11 Abs. 1 Z 6 UStG)",
        ))

    # §11 Abs. 1 Z 7 — delivery date OR service period
    #   (ebInterface: Delivery/Date or Delivery/Period/FromDate + ToDate)
    has_delivery_date = invoice.delivery_date is not None
    has_service_period = invoice.service_period_from is not None and invoice.service_period_to is not None
    if not has_delivery_date and not has_service_period:
        failures.append(ComplianceFailure(
            field="delivery_date",
            reason=(
                "Neither delivery date nor service period is set — "
                "one is required (§11 Abs. 1 Z 7 UStG)"
            ),
        ))

    # §11 Abs. 1 Z 8 — quantity and description of goods/services
    #   (ebInterface: ListLineItem/Description + ListLineItem/Quantity)
    if not invoice.line_items:
        failures.append(ComplianceFailure(
            field="line_items",
            reason="No line items present — quantity and description required (§11 Abs. 1 Z 8 UStG)",
        ))
    elif any(not item.description or item.qty <= 0 for item in invoice.line_items):
        failures.append(ComplianceFailure(
            field="line_items",
            reason="One or more line items missing description or positive quantity (§11 Abs. 1 Z 8 UStG)",
        ))

    # §11 Abs. 1 Z 9 — net amount broken down by VAT rate (ebInterface: TaxItem/TaxableAmount)
    if invoice.net_total < 0:
        failures.append(ComplianceFailure(
            field="net_total",
            reason="Net amount is negative (§11 Abs. 1 Z 9 UStG)",
        ))

    # §11 Abs. 1 Z 10 — applicable VAT rate (ebInterface: TaxItem/TaxPercent)
    if invoice.vat_rate < 0:
        failures.append(ComplianceFailure(
            field="vat_rate",
            reason="VAT rate is negative (§11 Abs. 1 Z 10 UStG)",
        ))

    # §11 Abs. 1 Z 11 — VAT amount (ebInterface: TaxItem/TaxAmount)
    expected_vat = round(invoice.net_total * invoice.vat_rate, 2)
    if abs(invoice.vat_amount - expected_vat) > 0.01:
        failures.append(ComplianceFailure(
            field="vat_amount",
            reason=(
                f"VAT amount {invoice.vat_amount} does not match "
                f"net_total × vat_rate = {expected_vat} (§11 Abs. 1 Z 11 UStG)"
            ),
        ))

    return ComplianceResult(passed=len(failures) == 0, failures=failures)
