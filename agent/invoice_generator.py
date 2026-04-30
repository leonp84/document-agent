"""Deterministic QuoteModel → InvoiceModel mapping. No LLM involved."""
from datetime import date

from agent.models import BusinessProfile, InvoiceModel, QuoteModel


def build_invoice(
    quote: QuoteModel,
    profile: BusinessProfile,
    invoice_number: str,
    invoice_date: date,
    delivery_date: date | None = None,
    service_period_from: date | None = None,
    service_period_to: date | None = None,
) -> InvoiceModel:
    client = quote.client
    return InvoiceModel(
        client=client,
        client_ref=quote.client_ref,
        line_items=quote.line_items,
        net_total=quote.net_total,
        vat_rate=quote.vat_rate,
        vat_amount=quote.vat_amount,
        gross_total=quote.gross_total,
        payment_terms=quote.payment_terms,
        language=quote.language,
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        delivery_date=delivery_date,
        service_period_from=service_period_from,
        service_period_to=service_period_to,
        supplier_name=profile.name,
        supplier_address_line1=profile.address_line1,
        supplier_address_line2=profile.address_line2,
        supplier_uid=profile.uid,
        recipient_name=client.name if client else "",
        recipient_address_line1=client.address_line1 if client else "",
        recipient_address_line2=client.address_line2 if client else "",
        recipient_uid=client.uid if client else None,
    )
