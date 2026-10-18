"""A&T Company Mods eligibility: provider-specific OR thresholds.

This module evaluates already-verified, deduplicated provider totals.
It does not ingest jobs, change mileage, grant Discord roles, or expose downloads.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

TRUCKSBOOK_REAL_MILES_REQUIRED = Decimal("40000")
TRUCKY_RACE_MILES_REQUIRED = Decimal("60000")


@dataclass(frozen=True)
class CompanyModsEligibility:
    eligible: bool
    qualifying_provider: str | None
    trucksbook_real_miles: Decimal
    trucky_race_miles: Decimal


def _miles(value):
    """Accept exact integer/Decimal inputs; reject floats and booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise ValueError("Mileage totals must be exact integer or Decimal values")
    try:
        miles = Decimal(value)
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError("Mileage total is invalid") from error
    if not miles.is_finite() or miles < 0:
        raise ValueError("Mileage totals must be finite and nonnegative")
    return miles


def evaluate_company_mods_eligibility(
    *,
    trucksbook_real_miles: int | Decimal,
    trucky_race_miles: int | Decimal,
) -> CompanyModsEligibility:
    """Evaluate exact provider totals without rounding fractional mileage.

    Callers must verify provider ownership and job deduplication upstream.
    Never substitute combined Real Miles or TrucksBook Race Miles.
    """
    tb_miles = _miles(trucksbook_real_miles)
    tr_miles = _miles(trucky_race_miles)
    trucksbook_qualified = tb_miles >= TRUCKSBOOK_REAL_MILES_REQUIRED
    trucky_qualified = tr_miles >= TRUCKY_RACE_MILES_REQUIRED
    provider = ("both" if trucksbook_qualified and trucky_qualified
                else "trucksbook" if trucksbook_qualified
                else "trucky" if trucky_qualified else None)
    return CompanyModsEligibility(
        eligible=provider is not None,
        qualifying_provider=provider,
        trucksbook_real_miles=tb_miles,
        trucky_race_miles=tr_miles,
    )
