"""Deterministic delivery reconciliation policy, independent of Discord and DB.

Provider job IDs are scoped to each provider. Cross-provider matches are
potential duplicates only; uncertain records must never be auto-credited.
"""
from datetime import timedelta
from unified_job_matching_v2 import is_candidate_duplicate


def classify_delivery(incoming, other_provider_jobs, *, provider_watermark=None, provider_feed_complete=False):
    """Return 'duplicate', 'creditable', or 'pending'.

    creditable requires a trusted watermark proving the other provider's
    delivery feed is complete beyond the incoming matching window. Caller
    must additionally enforce transactional idempotency before any award.
    """
    if incoming.get("provider") not in ("trucky", "trucksbook"):
        raise ValueError("Unsupported provider")
    if incoming.get("mileage_type") not in ("real", "race"):
        return "pending"
    if incoming["provider"] == "trucksbook" and incoming["mileage_type"] == "race":
        return "pending"  # TrucksBook race miles are not credited by A&T policy.
    delivered = incoming.get("delivered_at")
    if delivered is None or getattr(delivered, "tzinfo", None) is None:
        return "pending"
    if not isinstance(other_provider_jobs, (list, tuple)):
        raise ValueError("Expected other-provider job collection")
    for candidate in other_provider_jobs:
        try:
            if is_candidate_duplicate(incoming, candidate):
                return "duplicate" if candidate.get("credited") is True else "pending"
        except (ValueError, KeyError, TypeError):
            return "pending"
    # Time alone does not establish completeness of the other provider feed.
    if provider_feed_complete is not True:
        return "pending"
    if provider_watermark is None or getattr(provider_watermark, "tzinfo", None) is None:
        return "pending"
    from datetime import timezone
    if provider_watermark.astimezone(timezone.utc) < delivered.astimezone(timezone.utc) + timedelta(minutes=30):
        return "pending"
    return "creditable"
