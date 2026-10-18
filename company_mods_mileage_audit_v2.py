"""Read-only Company Mods mileage evidence inspection.

Confirmed matches alone do not establish that provider history is complete.
This audit deliberately does not grant access or change roles.
"""
from decimal import Decimal

QUERY = """
SELECT e.provider, e.real_miles, e.race_miles, m.review_status
FROM unified_provider_job_events AS e
JOIN unified_provider_links AS l
  ON l.discord_user_id=e.discord_user_id
 AND l.provider=e.provider AND l.verified_at IS NOT NULL
LEFT JOIN unified_job_matches AS m
  ON m.provider=e.provider AND m.provider_job_id=e.provider_job_id
WHERE e.discord_user_id=$1
"""

async def audit_company_mods_mileage(pool, discord_user_id):
    if type(discord_user_id) is not int or discord_user_id <= 0:
        raise ValueError("Invalid Discord user ID")
    async with pool.acquire() as conn:
        rows = await conn.fetch(QUERY, discord_user_id)
    tb_real = Decimal("0")
    trucky_race = Decimal("0")
    pending = 0
    rejected = 0
    for row in rows:
        status = row["review_status"]
        if status == "rejected":
            rejected += 1
            continue
        if status != "confirmed":
            pending += 1
            continue
        if row["provider"] == "trucksbook":
            tb_real += Decimal(str(row["real_miles"]))
        elif row["provider"] == "trucky":
            trucky_race += Decimal(str(row["race_miles"]))
    return {
        "trucksbook_confirmed_real_miles": tb_real,
        "trucky_confirmed_race_miles": trucky_race,
        "unreviewed_job_count": pending,
        "rejected_job_count": rejected,
        "history_complete": False,
        "auto_unlock_allowed": False,
        "reason": "Verified provider history completeness and cross-provider reconciliation not yet established",
    }
