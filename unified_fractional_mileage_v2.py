"""Lossless fractional mileage accumulation for the existing BIGINT driver totals.

Keep per-driver carry as Decimal; credit only completed whole miles.
Never round each job individually, which would systematically lose distance.
"""
from decimal import Decimal, InvalidOperation, ROUND_FLOOR


def accumulate_miles(carry_miles, new_miles):
    """Return (whole_miles_to_credit, new_fractional_carry)."""
    try:
        carry = Decimal(str(carry_miles))
        miles = Decimal(str(new_miles))
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise ValueError("Invalid mileage amount") from exc
    if not carry.is_finite() or not miles.is_finite():
        raise ValueError("Mileage must be finite")
    if carry < 0 or carry >= 1 or miles <= 0:
        raise ValueError("Carry must be within [0,1), job miles positive")
    total = carry + miles
    whole = int(total.to_integral_value(rounding=ROUND_FLOOR))
    remainder = total - Decimal(whole)
    return whole, remainder
