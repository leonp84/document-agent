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
