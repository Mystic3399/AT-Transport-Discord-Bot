"""Stage conservative cross-provider duplicate candidates for human review.

No mileage is credited, deleted, or merged by this module.
"""
import json
from unified_job_matching_v2 import is_candidate_duplicate


async def suggest_cross_provider_matches(pool, provider, provider_job_id):
    """Return staged candidate keys; require trusted complete metadata.

    This is read-only: a separate, audited reconciliation decision is required
    before assigning canonical keys or crediting driver mileage.
    """
    if provider not in ("trucky", "trucksbook") or not provider_job_id:
        raise ValueError("Invalid provider job reference")
    async with pool.acquire() as conn:
        primary = await conn.fetchrow("""
            SELECT provider,provider_job_id,discord_user_id,real_miles,race_miles,
                   delivered_at,source_payload
            FROM unified_provider_job_events
            WHERE provider=$1 AND provider_job_id=$2
        """, provider, provider_job_id)
        if primary is None:
            raise ValueError("Provider job not found")
        other = "trucksbook" if provider == "trucky" else "trucky"
        candidates = await conn.fetch("""
            SELECT provider,provider_job_id,discord_user_id,real_miles,race_miles,
                   delivered_at,source_payload
            FROM unified_provider_job_events
            WHERE provider=$1 AND discord_user_id=$2
              AND delivered_at BETWEEN $3::timestamptz - INTERVAL '30 minutes'
                                   AND $3::timestamptz + INTERVAL '30 minutes'
            ORDER BY delivered_at,provider_job_id
            LIMIT 100
        """, other, primary["discord_user_id"], primary["delivered_at"]) if primary["delivered_at"] else []

    def record(row):
        payload = row["source_payload"] or {}
        if isinstance(payload, str):
            payload = json.loads(payload)
        kind = payload.get("mileage_type")
        return {
            "provider": row["provider"],
            "discord_user_id": row["discord_user_id"],
            "mileage_type": kind,
            "metadata": payload.get("job_metadata"),
            "delivered_at": row["delivered_at"],
            "miles": row["real_miles"] if kind == "real" else row["race_miles"],
        }

    try:
        base = record(primary)
        if base["metadata"] is None or base["delivered_at"] is None:
            return []
        matches = []
        for candidate in candidates:
            try:
                if is_candidate_duplicate(base, record(candidate)):
                    matches.append((candidate["provider"], candidate["provider_job_id"]))
            except (ValueError, KeyError, TypeError):
                continue
        return matches
    except (ValueError, KeyError, TypeError):
        return []
