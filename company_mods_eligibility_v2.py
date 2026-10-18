"""A&T Company Mods eligibility: provider-specific OR thresholds.

This module evaluates already-verified, deduplicated provider totals.
It does not ingest jobs, change mileage, grant Discord roles, or expose downloads.
"""
from dataclasses import dataclass

TRUCKSBOOK_REAL_MILES_REQUIRED = 40_000
TRUCKY_RACE_MILES_REQUIRED = 60_000


@dataclass(frozen=True)
class CompanyModsEligibility:
    eligible: bool
    qualifying_provider: str | None
    trucksbook_real_miles: int
    trucky_race_miles: int


def evaluate_company_mods_eligibility(
    *,
    trucksbook_real_miles: int,
    trucky_race_miles: int,
) -> CompanyModsEligibility:
    """Return eligibility from independently verified, nonnegative mileage totals.

    Callers must verify provider ownership and job deduplication upstream.
    Never substitute combined Real Miles or TrucksBook Race Miles.
    """
    if any(type(value) is not int or value < 0 for value in
           (trucksbook_real_miles, trucky_race_miles)):
        raise ValueError("Mileage totals must be nonnegative integers")
    trucksbook_qualified = trucksbook_real_miles >= TRUCKSBOOK_REAL_MILES_REQUIRED
    trucky_qualified = trucky_race_miles >= TRUCKY_RACE_MILES_REQUIRED
    provider = ("both" if trucksbook_qualified and trucky_qualified
                else "trucksbook" if trucksbook_qualified
                else "trucky" if trucky_qualified else None)
    return CompanyModsEligibility(
        eligible=provider is not None,
        qualifying_provider=provider,
        trucksbook_real_miles=trucksbook_real_miles,
        trucky_race_miles=trucky_race_miles,
    )
