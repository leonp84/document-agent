"""Shared Pydantic models for the DocAssist agent pipeline."""
from datetime import date
from typing import Literal

from pydantic import BaseModel


class ServiceLine(BaseModel):
    description: str
    quantity: float | None = None
    unit: str | None = None
    rate: float | None = None  # null if not explicitly stated in input — never hallucinated


class ScopeModel(BaseModel):
    client_ref: str
    services: list[ServiceLine]
    vat_rate: float = 0.20
    language: Literal["de", "en"] = "de"
    confidence: Literal["high", "low"] = "high"


# --- Client ---

class ClientRecord(BaseModel):
    id: str
    name: str
    short_names: list[str] = []
    address_line1: str
    address_line2: str
    uid: str | None = None
    email: str | None = None


# --- Business profile ---

class DefaultRates(BaseModel):
    labor_hourly: float
    labor_daily: float
    material_markup_pct: float


class BusinessProfile(BaseModel):
    name: str
    address_line1: str
    address_line2: str
    uid: str
    logo_path: str = ""
    bank_iban: str
    bank_bic: str
    brand_color: str = "#000000"
    language: Literal["de", "en"] = "de"
    industry: Literal["Reinigung", "Handwerk", "Beratung"]
    default_rates: DefaultRates


# --- Rate resolution output ---

class ResolvedServiceLine(BaseModel):
    description: str
    quantity: float | None
    unit: str | None
    rate: float  # always present — never None


class UnresolvedServiceLine(BaseModel):
    description: str
    quantity: float | None
    unit: str | None


class ResolvedScope(BaseModel):
    client: ClientRecord | None  # None if lookup found no match
    client_ref: str              # original ref from extraction
    resolved: list[ResolvedServiceLine]
    unresolved: list[UnresolvedServiceLine]  # need clarification before generation
    vat_rate: float
    language: Literal["de", "en"]


# --- Quote ---

class QuoteLineItem(BaseModel):
    description: str
    qty: float
    unit: str
    rate: float
    amount: float  # qty * rate, always deterministic


class QuoteModel(BaseModel):
    client: ClientRecord | None
    client_ref: str
    line_items: list[QuoteLineItem]
    net_total: float
    vat_rate: float
    vat_amount: float
    gross_total: float
    payment_terms: str
    language: Literal["de", "en"]


# --- Invoice ---

class InvoiceModel(BaseModel):
    # Carried from QuoteModel
    client: ClientRecord | None
    client_ref: str
    line_items: list[QuoteLineItem]
    net_total: float
    vat_rate: float
    vat_amount: float
    gross_total: float
    payment_terms: str
    language: Literal["de", "en"]
    # Invoice identity (§11 Abs. 1 Z 5–6 UStG)
    invoice_number: str
    invoice_date: date
    # Delivery / service period (§11 Abs. 1 Z 7 UStG) — one branch must be set
    delivery_date: date | None = None
    service_period_from: date | None = None
    service_period_to: date | None = None
    # Supplier (§11 Abs. 1 Z 1, 3 UStG) — flattened from BusinessProfile
    supplier_name: str
    supplier_address_line1: str
    supplier_address_line2: str
    supplier_uid: str
    # Recipient (§11 Abs. 1 Z 2, 4 UStG) — flattened from ClientRecord
    recipient_name: str
    recipient_address_line1: str
    recipient_address_line2: str
    recipient_uid: str | None = None


# --- Compliance ---

class ComplianceFailure(BaseModel):
    field: str    # e.g. "invoice_number", "supplier_uid"
    reason: str   # human-readable; used as correction prompt context in Phase 7


class ComplianceResult(BaseModel):
    passed: bool
    failures: list[ComplianceFailure]
