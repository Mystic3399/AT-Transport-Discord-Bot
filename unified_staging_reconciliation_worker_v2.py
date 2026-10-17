"""Fail-closed staging entry point for reconciled real-mile awards.

This module never modifies production mileage or Discord roles.
"""
from unified_reconciliation_policy_v2 import classify_delivery
from unified_canonical_journey_v2 import canonical_delivery_key
from unified_staging_atomic_awards_v2 import award_staging_real_miles


async def reconcile_and_stage_real_miles(
    pool, incoming, other_provider_jobs, *,
    provider_watermark=None, provider_feed_complete=False,
    shared_journey_id=None, shared_id_verified=False
):
    """Only a proven creditable delivery can reach the staging award ledger.

    An unknown/late opposite-provider feed means pending, not credit.
    Similar route/time records are candidates, not verified shared IDs.
    """
    # Explicitly quarantined Discord observations must never reach awards,
    # even when a caller mistakenly supplies complete-feed flags.
    if (incoming.get("verified_delivery") is False
            or incoming.get("eligible_for_award") is False
            or incoming.get("trusted") is False):
        return {"status": "unverified_observation", "credited_whole_miles": 0}
    decision = classify_delivery(
        incoming, other_provider_jobs,
        provider_watermark=provider_watermark,
        provider_feed_complete=provider_feed_complete,
    )
    if decision != "creditable":
        return {"status": decision, "credited_whole_miles": 0}
    if incoming.get("mileage_type") != "real":
        return {"status": "unsupported_race_award", "credited_whole_miles": 0}
    # Never treat caller-provided completeness flags as authenticated proof.
    # This worker is staging-only; production must source them from a trusted feed.
    provider = incoming["provider"]
    job_id = incoming.get("provider_job_id")
    driver_id = incoming["discord_user_id"]
    key = canonical_delivery_key(
        provider, job_id,
        shared_journey_id=shared_journey_id,
        shared_id_verified=shared_id_verified,
    )
    return await award_staging_real_miles(
        pool, provider, job_id, driver_id, incoming["miles"],
        canonical_delivery_key=key,
    )
