"""Provider-neutral distance conversion for A&T V2 job ingestion.

Store canonical distances in miles. Convert once at the ingestion boundary,
before mileage classification, deduplication, totals, or Discord ranks.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

KM_PER_MILE = Decimal("1.609344")
MILES_PER_KM = Decimal("0.621371192237334")


def normalize_distance_miles(value, unit):
    """Return Decimal miles to 2 dp, rejecting ambiguous or invalid inputs.

    unit must be explicitly provided by the trusted provider adapter; never
    infer from a user's preferred display unit or a raw number.
    """
    if isinstance(value, bool) or value is None:
        raise ValueError("Invalid distance")
    if not isinstance(unit, str):
        raise ValueError("Distance unit is required")
    unit = unit.strip().lower()
    if unit in ("km", "kilometer", "kilometers", "kilometre", "kilometres"):
        factor = MILES_PER_KM
    elif unit in ("mi", "mile", "miles"):
        factor = Decimal("1")
    else:
        raise ValueError("Unknown distance unit; job must be held for review")
    try:
        distance = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("Invalid distance") from None
    if not distance.is_finite() or distance < 0:
        raise ValueError("Distance must be finite and non-negative")
    return (distance * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
