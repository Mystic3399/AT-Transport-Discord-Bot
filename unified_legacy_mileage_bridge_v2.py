"""Read-only compatibility bridge to the existing A&T real-mileage engine.

This module deliberately does not call bot.record_real_job: that legacy
function writes driver_progress immediately and cannot atomically reserve
a cross-provider canonical delivery. Integration must be transactional.
"""
from decimal import Decimal, InvalidOperation
from unified_reconciliation_policy_v2 import classify_delivery


def preview_legacy_real_miles(incoming, other_provider_jobs, *,
                              provider_watermark=None, provider_feed_complete=False):
    """Return an explicit dry-run award proposal; never update driver totals."""
    decision = classify_delivery(
        incoming, other_provider_jobs,
        provider_watermark=provider_watermark,
        provider_feed_complete=provider_feed_complete,
    )
    if decision != "creditable":
        return {"status": decision, "legacy_award_miles": 0}
    if incoming.get("mileage_type") != "real":
        return {"status": "unsupported_race_award", "legacy_award_miles": 0}
    try:
        miles = Decimal(str(incoming["miles"]))
    except (KeyError, ValueError, InvalidOperation, TypeError):
        return {"status": "pending", "legacy_award_miles": 0}
    if not miles.is_finite() or miles <= 0:
        return {"status": "pending", "legacy_award_miles": 0}
    # Legacy driver_progress uses BIGINT. Do not silently discard fractions:
    # preserve exact fractional miles until a rounding policy is approved.
    if miles != miles.to_integral_value():
        return {"status": "fractional_miles_pending", "legacy_award_miles": 0}
    return {
        "status": "ready_for_atomic_award",
        "legacy_award_miles": int(miles),
        "provider_job_key": (incoming["provider"], incoming.get("provider_job_id")),
        "discord_user_id": incoming["discord_user_id"],
    }
