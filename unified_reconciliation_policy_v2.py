"""Deterministic delivery reconciliation policy, independent of Discord and DB.

Provider job IDs are scoped to each provider. Cross-provider matches are
potential duplicates only; uncertain records must never be auto-credited.
"""
from datetime import timedelta
from unified_job_matching_v2 import is_candidate_duplicate, _normalized_metadata
from decimal import Decimal, InvalidOperation
import re


def _verified_shared_journey(first, second):
    """True only for an explicitly verified provider-independent journey ID."""
    shared_id = first.get("shared_journey_id")
    return (
        first.get("shared_id_verified") is True
        and second.get("shared_id_verified") is True
        and isinstance(shared_id, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", shared_id) is not None
        and second.get("shared_journey_id") == shared_id
    )


def classify_delivery(incoming, other_provider_jobs, *, provider_watermark=None, provider_feed_complete=False):
    """Return 'duplicate', 'creditable', or 'pending'.

    creditable requires a trusted watermark proving the other provider's
    delivery feed is complete beyond the incoming matching window. Caller
    must additionally enforce transactional idempotency before any award.
    """
    if incoming.get("provider") not in ("trucky", "trucksbook"):
        raise ValueError("Unsupported provider")
    if type(incoming.get("discord_user_id")) is not int or incoming["discord_user_id"] <= 0:
        return "pending"
    if (incoming.get("trusted") is False
            or incoming.get("verified_delivery") is False
            or incoming.get("eligible_for_award") is False):
        return "pending"
    if incoming.get("mileage_type") not in ("real", "race"):
        return "pending"
    if incoming["provider"] == "trucksbook" and incoming["mileage_type"] == "race":
        return "pending"  # TrucksBook race miles are not credited by A&T policy.
    try:
        _normalized_metadata(incoming.get("metadata"))
        miles = Decimal(str(incoming.get("miles")))
        if not miles.is_finite() or miles <= 0:
            return "pending"
    except (ValueError, TypeError, InvalidOperation):
        return "pending"
    delivered = incoming.get("delivered_at")
    if delivered is None or getattr(delivered, "tzinfo", None) is None:
        return "pending"
    if not isinstance(other_provider_jobs, (list, tuple)):
        raise ValueError("Expected other-provider job collection")
    ambiguous_candidate = False
    for candidate in other_provider_jobs:
        try:
            # A candidate from the wrong provider or driver is not evidence
            # that the opposite provider feed has been completely inspected.
            if not isinstance(candidate, dict):
                return "pending"
            if candidate.get("provider") not in ("trucky", "trucksbook") or candidate.get("provider") == incoming["provider"]:
                return "pending"
            if candidate.get("discord_user_id") != incoming["discord_user_id"]:
                continue
            # A verified journey identity survives changes to distance or route labels.
            if _verified_shared_journey(incoming, candidate):
                if (candidate.get("credited") is True
                        and incoming["mileage_type"] == candidate.get("mileage_type")):
                    return "duplicate"
                ambiguous_candidate = True
                continue
            # A Real/Race disagreement on an otherwise matching delivery
            # requires review before either record is treated as independent.
            comparison = candidate
            if candidate.get("mileage_type") in ("real", "race"):
                comparison = dict(candidate, mileage_type=incoming["mileage_type"])
            if is_candidate_duplicate(incoming, comparison):
                ambiguous_candidate = True
        except (ValueError, KeyError, TypeError, InvalidOperation):
            return "pending"
    if ambiguous_candidate:
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


def automatic_credit_selection(incoming, other_provider_jobs, *, provider_watermark=None,
                               provider_feed_complete=False):
    """Select one eligible Real record after the opposite feed is complete."""
    if incoming.get("mileage_type") != "real":
        return {"status": "pending", "reason": "real_jobs_required"}
    if any(incoming.get(key) is False for key in
           ("trusted", "verified_delivery", "eligible_for_award")):
        return {"status": "pending", "reason": "incoming_evidence_incomplete"}
    try:
        incoming_miles = Decimal(str(incoming["miles"]))
        if not incoming_miles.is_finite() or incoming_miles <= 0:
            raise ValueError("Invalid mileage")
        _normalized_metadata(incoming.get("metadata"))
        matches = []
        for other in other_provider_jobs:
            if (other.get("provider") not in ("trucky", "trucksbook")
                    or other["provider"] == incoming.get("provider")
                    or Decimal(str(other["miles"])) <= 0):
                return {"status": "pending", "reason": "comparison_evidence_incomplete"}
            if any(other.get(key) is False for key in
                   ("trusted", "verified_delivery", "eligible_for_award")):
                return {"status": "pending", "reason": "comparison_evidence_incomplete"}
            comparable = dict(other, mileage_type="real")
            if is_candidate_duplicate(incoming, comparable, max_miles_delta=None):
                if other.get("mileage_type") != "real":
                    return {"status": "pending", "reason": "classification_conflict"}
                matches.append(other)
        if len(matches) > 1:
            return {"status": "pending", "reason": "ambiguous_journey"}
        # A completed feed is required even for a match to exclude a second
        # delivery that could make the apparent one-to-one pairing ambiguous.
        readiness = classify_delivery(incoming, [], provider_watermark=provider_watermark,
                                      provider_feed_complete=provider_feed_complete)
        if readiness != "creditable":
            return {"status": "pending", "reason": "provider_feed_incomplete"}
        if not matches:
            return {"status": "independent", "winner": incoming}
        other = matches[0]
        winner = max((incoming, other), key=lambda job: (
            Decimal(str(job["miles"])), job["provider"] == "trucksbook"))
        return {"status": "matched", "winner": winner,
                "suppressed": other if winner is incoming else incoming}
    except (ValueError, TypeError, KeyError, ArithmeticError):
        return {"status": "pending", "reason": "comparison_evidence_incomplete"}
