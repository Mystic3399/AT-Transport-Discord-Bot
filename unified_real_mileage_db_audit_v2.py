"""Read-only PostgreSQL adapter for unified Real Miles reconciliation.

Requires verified provider links and explicitly confirmed canonical matches.
Does not grant roles, mutate totals, or establish complete provider history.
"""
from unified_real_mileage_audit_v2 import summarize_confirmed_real_miles

QUERY = """
SELECT e.provider, e.real_miles, e.race_miles,
       m.canonical_job_key, m.review_status
FROM unified_provider_job_events AS e
JOIN unified_provider_links AS l
  ON l.discord_user_id=e.discord_user_id
 AND l.provider=e.provider AND l.verified_at IS NOT NULL
LEFT JOIN unified_job_matches AS m
  ON m.provider=e.provider AND m.provider_job_id=e.provider_job_id
WHERE e.discord_user_id=$1
"""

async def audit_unified_real_mileage(pool, discord_user_id):
    if type(discord_user_id) is not int or discord_user_id <= 0:
        raise ValueError("Invalid Discord user ID")
    async with pool.acquire() as conn:
        records = await conn.fetch(QUERY, discord_user_id)
    rows = []
    for record in records:
        row = dict(record)
        from decimal import Decimal, InvalidOperation
        try:
            real = Decimal(str(row["real_miles"]))
            race = Decimal(str(row["race_miles"]))
            valid = real.is_finite() and race.is_finite() and real > 0 and race == 0
        except (InvalidOperation, ValueError, TypeError):
            valid = False
        row["mileage_type"] = "real" if valid else "excluded"
        rows.append(row)
    result = summarize_confirmed_real_miles(rows)
    result["reason"] = "Provider feed completeness and trusted canonical reconciliation not established"
    return result
