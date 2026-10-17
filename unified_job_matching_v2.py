"""Conservative cross-provider job matching for review, not mileage credit.

Only a verified same-driver, same-game, same-origin/destination, same-cargo
candidate within the configured delivery-time window may be suggested.
A matching suggestion NEVER automatically merges or credits jobs.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

REQUIRED_FIELDS = ("game", "origin", "destination", "cargo")


def _normalized_metadata(metadata):
    if not isinstance(metadata, dict):
        raise ValueError("Trusted job metadata is required")
    normalized = {}
    for key in REQUIRED_FIELDS:
        value = metadata.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Missing trusted job metadata: " + key)
        normalized[key] = " ".join(value.casefold().split())
    return normalized


def _utc(dt):
    if not isinstance(dt, datetime) or dt.tzinfo is None:
        raise ValueError("Timezone-aware delivery timestamp required")
    return dt.astimezone(timezone.utc)


def is_candidate_duplicate(first, second, *, max_minutes=30, max_miles_delta="5.00"):
    """Suggest a duplicate only when both records are fully comparable.

    Never match two records from the same provider; provider-job-id deduplication
    handles those independently. Unknown fields fail closed.
    """
    if first["provider"] == second["provider"]:
        return False
    if first["discord_user_id"] != second["discord_user_id"]:
        return False
    if first["mileage_type"] not in ("real", "race"):
        return False
    if first["mileage_type"] != second["mileage_type"]:
        return False
    if _normalized_metadata(first["metadata"]) != _normalized_metadata(second["metadata"]):
        return False
    if abs(_utc(first["delivered_at"]) - _utc(second["delivered_at"])) > timedelta(minutes=max_minutes):
        return False
    a = Decimal(str(first["miles"]))
    b = Decimal(str(second["miles"]))
    if not a.is_finite() or not b.is_finite() or a < 0 or b < 0:
        raise ValueError("Invalid mileage")
    return abs(a - b) <= Decimal(str(max_miles_delta))
