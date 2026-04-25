"""Rate resolution: fill missing service line rates from business profile defaults."""
from agent.models import (
    BusinessProfile,
    ClientRecord,
    DefaultRates,
    ResolvedScope,
    ResolvedServiceLine,
    ScopeModel,
    UnresolvedServiceLine,
)

_UNIT_TO_DEFAULT: dict[str, str] = {
    "Stunden": "labor_hourly",
    "Tage": "labor_daily",
}


def _default_for_unit(unit: str | None, rates: DefaultRates) -> float | None:
    """Return the profile default rate for a unit, or None if no default applies."""
    if unit is None:
        return None
    key = _UNIT_TO_DEFAULT.get(unit)
    if key is None:
        return None  # "pauschal" lump sums have no meaningful per-unit default
    return getattr(rates, key)


def resolve_rates(
    scope: ScopeModel,
    profile: BusinessProfile,
    client: ClientRecord | None = None,
) -> ResolvedScope:
    """
    Resolve every service line to a concrete rate.

    Lines with an explicit rate → kept as-is.
    Lines with null rate + resolvable unit → filled from profile.default_rates.
    Lines with null rate + unresolvable unit → added to ResolvedScope.unresolved.
    """
    resolved: list[ResolvedServiceLine] = []
    unresolved: list[UnresolvedServiceLine] = []

    for svc in scope.services:
        if svc.rate is not None:
            resolved.append(
                ResolvedServiceLine(
                    description=svc.description,
                    quantity=svc.quantity,
                    unit=svc.unit,
                    rate=svc.rate,
                )
            )
        else:
            default = _default_for_unit(svc.unit, profile.default_rates)
            if default is not None:
                resolved.append(
                    ResolvedServiceLine(
                        description=svc.description,
                        quantity=svc.quantity,
                        unit=svc.unit,
                        rate=default,
                    )
                )
            else:
                unresolved.append(
                    UnresolvedServiceLine(
                        description=svc.description,
                        quantity=svc.quantity,
                        unit=svc.unit,
                    )
                )

    return ResolvedScope(
        client=client,
        client_ref=scope.client_ref,
        resolved=resolved,
        unresolved=unresolved,
        vat_rate=scope.vat_rate,
        language=scope.language,
    )
