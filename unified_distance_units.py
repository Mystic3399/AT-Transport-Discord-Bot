"""Strict unit conversion for trusted provider job payloads.

Do not guess a missing unit; reject ambiguous records for review.
All accounting values are Decimal and stored in miles at two decimal places.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

KM_TO_MILES = Decimal("0.621371192237334")
TWO_PLACES = Decimal("0.01")
UNIT_ALIASES = {
    "km": "km", "kilometer": "km", "kilometers": "km",
    "kilometre": "km", "kilometres": "km",
    "mi": "mi", "mile": "mi", "miles": "mi",
}


def normalize_distance(value, unit):
    """Return (original Decimal, canonical unit, miles Decimal)."""
    if not isinstance(unit, str) or unit.strip().lower() not in UNIT_ALIASES:
        raise ValueError("Missing or unsupported distance unit")
    canonical = UNIT_ALIASES[unit.strip().lower()]
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError("Invalid distance")
    try:
        original = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid distance") from exc
    if not original.is_finite() or original < 0:
        raise ValueError("Distance must be finite and nonnegative")
    miles = original * (KM_TO_MILES if canonical == "km" else Decimal(1))
    return original, canonical, miles.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def normalize_job_distances(*, real_distance, real_unit, race_distance=0, race_unit=None):
    """Normalize real and race mileage independently; no implicit unit fallback."""
    real = normalize_distance(real_distance, real_unit)
    race = normalize_distance(race_distance, race_unit or real_unit)
    return {
        "real_original": real[0], "real_unit": real[1], "real_miles": real[2],
        "race_original": race[0], "race_unit": race[1], "race_miles": race[2],
    }
