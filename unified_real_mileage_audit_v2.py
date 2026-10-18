"""Conservative read-only unified Real Miles reconciliation.

Never awards mileage, mutates a database, or claims provider history is complete.
Confirmed cross-provider records sharing a canonical job key count once.
Unreviewed, rejected, malformed and unkeyed records are excluded.
"""
from decimal import Decimal, InvalidOperation


def summarize_confirmed_real_miles(rows):
    if not isinstance(rows, (list, tuple)):
        raise ValueError("Expected provider job rows")
    unique = {}
    pending = rejected = invalid = duplicates = 0
    for row in rows:
        if not isinstance(row, dict):
            invalid += 1
            continue
        status = row.get("review_status")
        if status == "rejected":
            rejected += 1
            continue
        if status != "confirmed":
            pending += 1
            continue
        provider = row.get("provider")
        category = row.get("mileage_type")
        if provider not in ("trucksbook", "trucky") or category != "real":
            continue
        key = row.get("canonical_job_key")
        if not isinstance(key, str) or not key.strip():
            invalid += 1
            continue
        try:
            miles = Decimal(str(row.get("real_miles")))
        except (InvalidOperation, ValueError, TypeError):
            invalid += 1
            continue
        if not miles.is_finite() or miles <= 0:
            invalid += 1
            continue
        if key in unique:
            duplicates += 1
            # Different provider distances can disagree: conservatively use
            # the smaller confirmed distance, never the sum.
            unique[key] = min(unique[key], miles)
        else:
            unique[key] = miles
    return {
        "confirmed_unique_real_miles": sum(unique.values(), Decimal("0")),
        "unique_job_count": len(unique),
        "cross_provider_repeat_count": duplicates,
        "unreviewed_job_count": pending,
        "rejected_job_count": rejected,
        "invalid_job_count": invalid,
        "history_complete": False,
        "auto_award_allowed": False,
    }
