"""Unit tests for agent.rate_resolver — no LLM involved."""
import pytest

from agent.models import (
    BusinessProfile,
    DefaultRates,
    ScopeModel,
    ServiceLine,
)
from agent.rate_resolver import resolve_rates

_PROFILE = BusinessProfile(
    name="Test GmbH",
    address_line1="Testgasse 1",
    address_line2="1010 Wien",
    uid="ATU11111111",
    bank_iban="AT00 0000 0000 0000 0000",
    bank_bic="TESTBIC",
    industry="Reinigung",
    default_rates=DefaultRates(
        labor_hourly=22.0,
        labor_daily=160.0,
        material_markup_pct=15.0,
    ),
)


def _scope(*lines: tuple, vat: float = 0.20) -> ScopeModel:
    """Build a ScopeModel from (description, quantity, unit, rate) tuples."""
    return ScopeModel(
        client_ref="Test Client",
        services=[
            ServiceLine(description=d, quantity=q, unit=u, rate=r)
            for d, q, u, r in lines
        ],
        vat_rate=vat,
    )


class TestAllRatesExplicit:
    def test_all_resolved(self):
        scope = _scope(
            ("Büroreinigung", 8, "Stunden", 24.0),
            ("Fensterreinigung", 4, "Stunden", 35.0),
        )
        result = resolve_rates(scope, _PROFILE)
        assert len(result.resolved) == 2
        assert len(result.unresolved) == 0

    def test_rates_preserved(self):
        scope = _scope(("Büroreinigung", 8, "Stunden", 24.0))
        result = resolve_rates(scope, _PROFILE)
        assert result.resolved[0].rate == 24.0

    def test_pauschal_with_explicit_rate_resolves(self):
        scope = _scope(("Material", 1, "pauschal", 680.0))
        result = resolve_rates(scope, _PROFILE)
        assert len(result.resolved) == 1
        assert result.resolved[0].rate == 680.0


class TestMissingRatesFilledFromProfile:
    def test_stunden_null_uses_labor_hourly(self):
        scope = _scope(("Treppenreinigung", 6, "Stunden", None))
        result = resolve_rates(scope, _PROFILE)
        assert len(result.resolved) == 1
        assert result.resolved[0].rate == 22.0

    def test_tage_null_uses_labor_daily(self):
        scope = _scope(("Beratung", 2, "Tage", None))
        result = resolve_rates(scope, _PROFILE)
        assert len(result.resolved) == 1
        assert result.resolved[0].rate == 160.0

    def test_description_and_quantity_preserved(self):
        scope = _scope(("Sockelmontage", 4, "Stunden", None))
        result = resolve_rates(scope, _PROFILE)
        svc = result.resolved[0]
        assert svc.description == "Sockelmontage"
        assert svc.quantity == 4
        assert svc.unit == "Stunden"


class TestUnresolvableLines:
    def test_pauschal_null_rate_is_unresolved(self):
        scope = _scope(("Material unbekannt", 1, "pauschal", None))
        result = resolve_rates(scope, _PROFILE)
        assert len(result.resolved) == 0
        assert len(result.unresolved) == 1

    def test_null_unit_null_rate_is_unresolved(self):
        scope = _scope(("Diverse Arbeiten", None, None, None))
        result = resolve_rates(scope, _PROFILE)
        assert len(result.unresolved) == 1

    def test_unresolved_preserves_description(self):
        scope = _scope(("Material pauschal", 1, "pauschal", None))
        result = resolve_rates(scope, _PROFILE)
        assert result.unresolved[0].description == "Material pauschal"


class TestMixedLines:
    def test_partial_rates(self):
        # hw_006 pattern: explicit daily + null hourly + explicit pauschal
        scope = _scope(
            ("Küchenmontage", 2, "Tage", 490.0),
            ("Sockelmontage", 4, "Stunden", None),
            ("Silikon und Kleinmaterial", 1, "pauschal", 65.0),
        )
        result = resolve_rates(scope, _PROFILE)
        assert len(result.resolved) == 3
        assert len(result.unresolved) == 0
        rates = {s.description: s.rate for s in result.resolved}
        assert rates["Küchenmontage"] == 490.0
        assert rates["Sockelmontage"] == 22.0
        assert rates["Silikon und Kleinmaterial"] == 65.0

    def test_some_unresolvable(self):
        scope = _scope(
            ("Beratung", 3, "Tage", None), # → resolved via labor_daily
            ("Sondermaterial", 1, "pauschal", None), # → unresolved
        )
        result = resolve_rates(scope, _PROFILE)
        assert len(result.resolved) == 1
        assert len(result.unresolved) == 1


class TestEmptyAndMetadata:
    def test_empty_services(self):
        scope = _scope()
        result = resolve_rates(scope, _PROFILE)
        assert result.resolved == []
        assert result.unresolved == []

    def test_client_ref_passed_through(self):
        scope = _scope(("Reinigung", 2, "Stunden", 22.0))
        result = resolve_rates(scope, _PROFILE)
        assert result.client_ref == "Test Client"

    def test_vat_rate_passed_through(self):
        scope = _scope(("Reinigung", 2, "Stunden", 22.0), vat=0.10)
        result = resolve_rates(scope, _PROFILE)
        assert result.vat_rate == 0.10

    def test_client_record_attached(self):
        from agent.models import ClientRecord
        client = ClientRecord(
            id="c1", name="Test GmbH", address_line1="Str 1",
            address_line2="1010 Wien",
        )
        scope = _scope(("Reinigung", 2, "Stunden", 22.0))
        result = resolve_rates(scope, _PROFILE, client=client)
        assert result.client is not None
        assert result.client.id == "c1"

    def test_no_client_match_is_none(self):
        scope = _scope(("Reinigung", 2, "Stunden", 22.0))
        result = resolve_rates(scope, _PROFILE, client=None)
        assert result.client is None
