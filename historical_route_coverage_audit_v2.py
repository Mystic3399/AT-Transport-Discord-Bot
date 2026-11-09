"""Read-only evidence coverage audit for held historical TrucksBook/Trucky Real jobs.

Run with a trusted, read-only database connection. No mileage writes, link
changes, inferred account ownership or automatic approvals are performed.
"""
from collections import defaultdict
from decimal import Decimal
import json

from automatic_linked_mileage_v2 import trucksbook_comparison
from unified_job_matching_v2 import _normalized_metadata


async def inspect_historical_route_coverage(conn, discord_user_id):
    """Report verified, comparable TrucksBook jobs and historical gaps per driver.

    Never interpret a missing webhook as proof that the TrucksBook job did
    not happen. Multiple versions are accepted only when *all* are comparable
    and agree on route, delivery time and credited distance.
    """
    async with conn.transaction(readonly=True, isolation="repeatable_read"):
        rows = await conn.fetch("""
            SELECT p.job_id,p.accepted_distance,h.evidence
            FROM processed_jobs p
            LEFT JOIN pending_provider_job_history_v2 h
              ON h.provider='trucksbook' AND h.provider_job_id=p.job_id
             AND h.source_kind='verified_webhook_source'
            WHERE p.discord_user_id=$1 AND LOWER(p.statistics)='real'
              AND p.accepted_distance>0
            ORDER BY p.job_id
        """, discord_user_id)
    grouped = defaultdict(list)
    for row in rows:
        grouped[str(row["job_id"])].append(row)
    counts = defaultdict(int)
    for job_id, versions in grouped.items():
        if any(row["evidence"] is None for row in versions):
            counts["missing_verified_webhook"] += 1
            continue
        comparable = []
        try:
            for row in versions:
                comparable.append(trucksbook_comparison(
                    job_id, discord_user_id, row["evidence"], row["accepted_distance"]))
        except (ValueError, TypeError, KeyError, ArithmeticError, json.JSONDecodeError):
            counts["invalid_or_incomplete_evidence"] += 1
            continue
        first = comparable[0]
        # Provider retries may add harmless extra fields or change formatting.
        # Compare the same canonical route fields used by reconciliation.
        if any(_normalized_metadata(item["metadata"]) != _normalized_metadata(first["metadata"])
               or item["delivered_at"] != first["delivered_at"]
               or Decimal(item["miles"]) != Decimal(first["miles"])
               for item in comparable[1:]):
            counts["conflicting_verified_versions"] += 1
        else:
            counts["comparable_verified_jobs"] += 1
    return {
        "processed_real_jobs": len(grouped),
        "comparable_verified_jobs": counts["comparable_verified_jobs"],
        "missing_verified_webhook": counts["missing_verified_webhook"],
        "invalid_or_incomplete_evidence": counts["invalid_or_incomplete_evidence"],
        "conflicting_verified_versions": counts["conflicting_verified_versions"],
        "mileage_changed": False,
    }
