"""Validate Trucky API job ownership against a verified A&T provider link.

This adapter does not award mileage or trust a user-submitted account ID.
"""
from decimal import Decimal, InvalidOperation

TRUCKY_COMPANY_ID = 49041


def normalize_verified_trucky_job(job, *, verified_provider_account_id, discord_user_id):
    """Return a review-only candidate, or None if job cannot be trusted."""
    if not isinstance(job, dict):
        return None
    if type(discord_user_id) is not int or discord_user_id <= 0:
        return None
    if type(job.get("company_id")) is not int or job["company_id"] != TRUCKY_COMPANY_ID:
        return None
    if type(job.get("id")) is not int or job["id"] <= 0:
        return None
    if type(job.get("user_id")) is not int or job["user_id"] <= 0:
        return None
    if not isinstance(verified_provider_account_id, str) or not verified_provider_account_id.isdecimal():
        return None
    if str(job["user_id"]) != verified_provider_account_id:
        return None
    if job.get("status") != "completed" or not isinstance(job.get("completed_at"), str) or not job["completed_at"]:
        return None
    if job.get("stats_type") != "real_miles" or job.get("distance_unit") != "km":
        return None
    try:
        km = Decimal(str(job.get("real_driven_distance_km")))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not km.is_finite() or km <= 0 or km > 100000:
        return None
    return {
        "provider": "trucky",
        "provider_job_id": str(job["id"]),
        "discord_user_id": discord_user_id,
        "mileage_type": "real",
        "kilometers": km,
        "verified_delivery": True,
        "eligible_for_award": False,
        "trusted": False,
    }
