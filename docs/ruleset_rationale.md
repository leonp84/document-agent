# Compliance Ruleset Rationale

§11 UStG field list with subsection reference, compliance_check field name, and ebInterface 6.1 element.

| # | §11 UStG | Description | `InvoiceModel` field(s) | ebInterface 6.1 element |
|---|----------|-------------|------------------------|-------------------------|
| 1 | Abs. 1 Z 1 | Supplier name and address | `supplier_name`, `supplier_address_line1`, `supplier_address_line2` | `Biller/Address/Name`, `Biller/Address/Street`, `Biller/Address/Town` |
| 2 | Abs. 1 Z 2 | Recipient name and address | `recipient_name`, `recipient_address_line1`, `recipient_address_line2` | `InvoiceRecipient/Address/Name`, `InvoiceRecipient/Address/Street`, `InvoiceRecipient/Address/Town` |
| 3 | Abs. 1 Z 3 | Supplier UID number (`ATU` + 8 digits) | `supplier_uid` | `Biller/VATIdentificationNumber` |
| 4 | Abs. 1 Z 4 | Recipient UID number (required when invoice > €10,000) | `recipient_uid` | `InvoiceRecipient/VATIdentificationNumber` |
| 5 | Abs. 1 Z 5 | Sequential invoice number | `invoice_number` | `InvoiceNumber` |
| 6 | Abs. 1 Z 6 | Invoice date | `invoice_date` | `InvoiceDate` |
| 7 | Abs. 1 Z 7 | Date of delivery or service period | `delivery_date` OR (`service_period_from` + `service_period_to`) | `Delivery/Date` OR `Delivery/Period/FromDate` + `Delivery/Period/ToDate` |
| 8 | Abs. 1 Z 8 | Quantity and description of goods/services | `line_items[*].description`, `line_items[*].qty` | `ListLineItem/Description`, `ListLineItem/Quantity` |
| 9 | Abs. 1 Z 9 | Net amount broken down by VAT rate | `net_total` | `TaxItem/TaxableAmount` |
| 10 | Abs. 1 Z 10 | Applicable VAT rate(s) | `vat_rate` | `TaxItem/TaxPercent` |
| 11 | Abs. 1 Z 11 | VAT amount | `vat_amount` | `TaxItem/TaxAmount` |

## Rule notes

**Field 4 — recipient UID threshold:** Austrian law requires the recipient's UID only when the invoice total exceeds €10,000. Below that threshold the field is optional and the check passes unconditionally.

**Field 6 — invoice date:** `invoice_date` is a required `date` field on `InvoiceModel`. Pydantic rejects construction without it, so the compliance check for this field is always satisfied when the model exists. It is listed for completeness and audit trail.

**Field 7 — delivery date vs. service period:** A single-day job sets `delivery_date`. A multi-day or retainer engagement sets `service_period_from` + `service_period_to`. Either branch satisfies the rule; both missing is a failure.

**Fields 9–11 — arithmetic consistency:** `net_total` and `vat_rate` are carried from `QuoteModel` where they were computed deterministically. The compliance check verifies `vat_amount` matches `round(net_total × vat_rate, 2)` within a €0.01 rounding tolerance.

**UID format:** Austrian UID numbers follow the pattern `ATU` followed by exactly 8 digits (regex `^ATU\d{8}$`). This applies to both supplier (always required) and recipient (conditional).

## ebInterface 6.1 XSD source

Schema fetched from `https://www.erechnung.gv.at/files/xsd/ebinterface-6.1-bund.xsd` (Austrian Federal Chancellery, Bundesministerium für Finanzen variant). Element names confirmed against the XSD `complexType` definitions for `BillerType`, `InvoiceRecipientType`, `DeliveryType`, `PeriodType`, `TaxItemType`, and `ListLineItemType`.
