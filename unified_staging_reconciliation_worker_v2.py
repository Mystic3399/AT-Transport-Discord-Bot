"""Fail-closed staging entry point for reconciled real-mile awards.

This module never modifies production mileage or Discord roles.
"""
from unified_reconciliation_policy_v2 import classify_delivery
from unified_mileage_engine_control_v2 import canonical_credit_key
from unified_staging_atomic_awards_v2 import award_staging_miles
from unified_mileage_engine_control_v2 import (
    feed_evidence_is_complete,
    mileage_engine_enabled,
    verified_job_evidence_reason,
)


async def reconcile_and_stage_miles(
    pool, incoming, other_provider_jobs, *,
    provider_watermark=None, provider_feed_complete=False,
    provider_feed_authenticated=False, provider_feed_snapshot_sha256=None,
    shared_journey_id=None, shared_id_verified=False
):
    """Only a proven creditable delivery can reach the staging award ledger.

    An unknown/late opposite-provider feed means pending, not credit.
    Similar route/time records are candidates, not verified shared IDs.
    """
    if not mileage_engine_enabled():
        return {"status": "mileage_engine_disabled", "credited_whole_miles": 0}
    if not isinstance(incoming, dict):
        return {"status": "pending", "reason": "invalid_record",
                "credited_whole_miles": 0}
    # Explicitly quarantined Discord observations must never reach awards,
    # even when a caller mistakenly supplies complete-feed flags.
    if (incoming.get("verified_delivery") is False
            or incoming.get("eligible_for_award") is False
            or incoming.get("trusted") is False):
        return {"status": "unverified_observation", "credited_whole_miles": 0}
    evidence_reason = verified_job_evidence_reason(incoming)
    if evidence_reason is not None:
        return {"status": "pending", "reason": evidence_reason,
                "credited_whole_miles": 0}
    if not isinstance(other_provider_jobs, (list, tuple)):
        return {"status": "pending", "reason": "opposite_provider_feed_invalid",
                "credited_whole_miles": 0}
    for candidate in other_provider_jobs:
        if not isinstance(candidate, dict):
            return {"status": "pending", "reason": "opposite_provider_record_invalid",
                    "credited_whole_miles": 0}
        if candidate.get("provider") == incoming["provider"]:
            return {"status": "pending", "reason": "opposite_provider_record_invalid",
                    "credited_whole_miles": 0}
        candidate_reason = verified_job_evidence_reason(candidate)
        if candidate_reason is not None:
            return {"status": "pending", "reason": "opposite_provider_record_unverified",
                    "credited_whole_miles": 0}
    if not feed_evidence_is_complete(
        authenticated=provider_feed_authenticated,
        complete=provider_feed_complete,
        watermark=provider_watermark,
        snapshot_sha256=provider_feed_snapshot_sha256,
    ):
        return {"status": "pending", "reason": "opposite_provider_feed_unverified",
                "credited_whole_miles": 0}
    # This staging worker has no provider-verifiable shared-ID attestation
    # adapter. A caller flag or a digest by itself is not proof, so any
    # proposed cross-provider identity must go through the auditable review
    # workflow and cannot enter the award ledger here.
    if shared_journey_id is not None or shared_id_verified is True:
        return {"status": "pending", "reason": "shared_journey_review_required",
                "credited_whole_miles": 0}
    decision = classify_delivery(
        incoming, other_provider_jobs,
        provider_watermark=provider_watermark,
        provider_feed_complete=provider_feed_complete,
    )
    if decision != "creditable":
        return {"status": decision, "credited_whole_miles": 0}
    # Never treat caller-provided completeness flags as authenticated proof.
    # This worker is staging-only; production must source them from a trusted feed.
    provider = incoming["provider"]
    job_id = incoming.get("provider_job_id")
    driver_id = incoming["discord_user_id"]
    key = canonical_credit_key(
        provider, job_id,
        shared_journey_id=None,
    )
    return await award_staging_miles(
        pool, provider, job_id, driver_id, incoming["miles"],
        mileage_type=incoming["mileage_type"],
        canonical_delivery_key=key,
    )


async def reconcile_and_stage_real_miles(*args, **kwargs):
    """Backward-compatible alias for existing real-mile callers."""
    incoming = args[1] if len(args) > 1 else kwargs.get("incoming", {})
    if incoming.get("mileage_type") != "real":
        return {"status": "unsupported_race_award", "credited_whole_miles": 0}
    return await reconcile_and_stage_miles(*args, **kwargs)
