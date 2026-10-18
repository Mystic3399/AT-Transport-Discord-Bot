"""Validate Trucky API job ownership against a verified A&T provider link.

This adapter does not award mileage or trust a user-submitted account ID.
"""
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

from unified_reconciliation_policy_v2 import classify_delivery

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
    # World of Trucks is an event tag, not a reason to discard Real Miles.
    # Keep this adapter read-only until provider API verification is complete.
    from world_of_trucks_policy_v2 import classify_event_mileage
    classification = classify_event_mileage("trucky", "Real, WoTr" if job.get("event_tag") == "WoTr" else "Real")
    if classification is None or classification.mileage_type != "real":
        return None
    try:
        km = Decimal(str(job.get("real_driven_distance_km")))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not km.is_finite() or km <= 0 or km > 100000:
        return None
    try:
        delivered_at = datetime.fromisoformat(job["completed_at"].replace("Z", "+00:00"))
    except ValueError:
        return None
    if delivered_at.tzinfo is None or delivered_at.astimezone(timezone.utc) > datetime.now(timezone.utc):
        return None
    return {
        "delivered_at": delivered_at,
        "provider": "trucky",
        "provider_job_id": str(job["id"]),
        "discord_user_id": discord_user_id,
        "mileage_type": "real",
        "kilometers": km,
        "verified_delivery": True,
        "eligible_for_award": False,
        "trusted": False,
    }



def review_trucky_job_against_other_provider(
    job, *, verified_provider_account_id, discord_user_id,
    other_provider_jobs, provider_watermark=None, provider_feed_complete=False
):
    """Read-only cross-provider decision. Never returns an award instruction.

    A caller cannot establish authenticated feed completeness simply by
    passing flags. The result remains pending even for a creditable policy
    decision until independent feed and ownership verification is implemented.
    """
    candidate = normalize_verified_trucky_job(
        job, verified_provider_account_id=verified_provider_account_id,
        discord_user_id=discord_user_id,
    )
    if candidate is None:
        return {"status": "rejected", "awardable": False}
    decision = classify_delivery(
        candidate, other_provider_jobs,
        provider_watermark=provider_watermark,
        provider_feed_complete=provider_feed_complete,
    )
    return {"status": decision, "awardable": False}
