"""A&T Company Mods eligibility: deduplicated combined Real OR Trucky Race thresholds.

This module evaluates already-verified, deduplicated provider totals.
It does not ingest jobs, change mileage, grant Discord roles, or expose downloads.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

COMBINED_REAL_MILES_REQUIRED = Decimal("40000")
TRUCKSBOOK_REAL_MILES_REQUIRED = COMBINED_REAL_MILES_REQUIRED  # compatibility alias
TRUCKY_RACE_MILES_REQUIRED = Decimal("60000")


@dataclass(frozen=True)
class CompanyModsEligibility:
    eligible: bool
    qualifying_provider: str | None
    trucksbook_real_miles: Decimal
    trucky_race_miles: Decimal
    combined_real_miles: Decimal


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
    trucksbook_real_miles: int | Decimal = 0,
    trucky_race_miles: int | Decimal = 0,
    combined_real_miles: int | Decimal | None = None,
) -> CompanyModsEligibility:
    """Evaluate exact provider totals without rounding fractional mileage.

    Callers must verify provider ownership and job deduplication upstream.
    Prefer the canonical ledger combined Real total: it already counts each
    accepted journey once. Legacy callers may supply TrucksBook-only totals.
    Never sum raw provider feeds here or include TrucksBook Race Miles.
    """
    tb_miles = _miles(trucksbook_real_miles)
    tr_miles = _miles(trucky_race_miles)
    combined = tb_miles if combined_real_miles is None else _miles(combined_real_miles)
    if combined < tb_miles:
        raise ValueError("Combined Real Miles cannot be less than TrucksBook Real Miles")
    trucksbook_qualified = combined >= COMBINED_REAL_MILES_REQUIRED
    trucky_qualified = tr_miles >= TRUCKY_RACE_MILES_REQUIRED
    provider = ("both" if trucksbook_qualified and trucky_qualified
                else ("combined_real" if combined_real_miles is not None else "trucksbook") if trucksbook_qualified
                else "trucky" if trucky_qualified else None)
    return CompanyModsEligibility(
        eligible=provider is not None,
        qualifying_provider=provider,
        trucksbook_real_miles=tb_miles,
        trucky_race_miles=tr_miles,
        combined_real_miles=combined,
    )

