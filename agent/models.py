"""Shared Pydantic models for the DocAssist agent pipeline."""
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
