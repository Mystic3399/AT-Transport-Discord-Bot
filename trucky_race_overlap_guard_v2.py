"""Conservative review of Trucky Race against already-credited TrucksBook Real.

Race and Real are different accounting buckets, but one physical delivery
must not be credited in both. Missing source routes or unexplained historical
balances are reasons to hold, not licence to add a second credit.
"""
from datetime import timedelta
from decimal import Decimal, ROUND_FLOOR

from automatic_linked_mileage_v2 import trucksbook_comparison
from unified_job_matching_v2 import _normalized_metadata


def possible_same_journey(trucky, trucksbook):
    """Cross-classification route/time comparison; do not require same bucket."""
    first = _normalized_metadata(trucky["job_metadata"])
    second = _normalized_metadata(trucksbook["metadata"])
    if first["game"] != second["game"]:
        return False
    minutes = abs(trucky["delivered_at"] - trucksbook["delivered_at"])
    if minutes > timedelta(minutes=30):
        return False
    # Cargo may be translated between trackers. In the same time window,
    # route equality is enough to require human review rather than double credit.
    return first["origin"] == second["origin"] and first["destination"] == second["destination"]


async def race_overlap_hold(conn, candidate):
    """Return a hold reason, or None when all existing TB history is comparable."""
    user = candidate["discord_user_id"]
    count = await conn.fetchval("""
        SELECT COUNT(*) FROM processed_jobs
        WHERE discord_user_id=$1 AND LOWER(statistics) LIKE 'real%'
          AND accepted_distance>0
    """, user)
    balance = await conn.fetchval(
        "SELECT real_miles FROM driver_progress WHERE discord_user_id=$1", user)
    already_trucky_real = await conn.fetchval("""
        SELECT COALESCE(SUM(miles),0) FROM unified_v2_production_credit_ledger
        WHERE discord_user_id=$1 AND provider='trucky' AND mileage_type='real'
    """, user)
    if not count:
        # Account may carry an imported TrucksBook Real balance even without
        # processed-job rows: never assume a zero historical overlap.
        if Decimal(balance or 0) > Decimal(already_trucky_real or 0).to_integral_value(rounding=ROUND_FLOOR):
            return "race_historical_balance_pending"
        return None
    if not candidate.get("job_metadata"):
        return "race_route_evidence_pending"
    rows = await conn.fetch("""
        SELECT p.job_id,p.accepted_distance,h.evidence
        FROM processed_jobs p
        LEFT JOIN pending_provider_job_history_v2 h
          ON h.provider='trucksbook' AND h.provider_job_id=p.job_id
         AND h.source_kind='verified_webhook_source'
         AND (
           EXISTS(SELECT 1 FROM driver_links d WHERE d.discord_user_id=p.discord_user_id
             AND d.trucksbook_user_id::text=h.provider_account_id
             AND d.trucksbook_name <> ('__unlinked_driver_'||d.discord_user_id::text||'__'))
           OR EXISTS(SELECT 1 FROM unified_provider_links l
             WHERE l.discord_user_id=p.discord_user_id AND l.provider='trucksbook'
               AND l.provider_account_id=h.provider_account_id AND l.verified_at IS NOT NULL)
         )
        WHERE p.discord_user_id=$1 AND LOWER(p.statistics) LIKE 'real%'
          AND p.accepted_distance>0
        ORDER BY p.job_id
    """, user)
    from automatic_mileage_source_recovery_v2 import (
        valid_temporal_trucksbook_observation,temporally_independent)
    import json
    observations = {}
    for row in rows:
        if row["evidence"] is None:
            return "race_historical_route_coverage_pending"
        evidence = (json.loads(row["evidence"]) if isinstance(row["evidence"],str)
                    else row["evidence"])
        try:
            comparison=trucksbook_comparison(row["job_id"],user,evidence,
                                             row["accepted_distance"])
        except (KeyError, TypeError, ValueError):
            # Time-only evidence is useful solely to rule out a duplicate
            # across a generous 2-hour uncertainty window. Without that
            # proof, the Race credit remains held.
            observed_at=valid_temporal_trucksbook_observation(
                evidence,row["accepted_distance"])
            if (observed_at is None or not temporally_independent(
                    candidate["delivered_at"],observed_at)):
                return "race_historical_route_coverage_pending"
            observations.setdefault(row["job_id"], []).append(None)
            continue
        observations.setdefault(row["job_id"], []).append(comparison)
    if len(observations) != count:
        return "race_historical_route_coverage_pending"
    for comparisons in observations.values():
        # A source without route fields was accepted only with all-time
        # separation proof above. Never infer its missing route details.
        if any(job is None for job in comparisons):
            if not all(job is None for job in comparisons):
                return "race_conflicting_route_evidence"
            continue
        # Conflicting retained versions are not evidence for an independent job.
        if len({(job["delivered_at"],str(job["metadata"])) for job in comparisons}) > 1:
            return "race_conflicting_route_evidence"
        try:
            if any(possible_same_journey(candidate, job) for job in comparisons):
                return "race_potential_duplicate"
        except (ValueError, TypeError, KeyError):
            return "race_route_evidence_pending"
    return None
