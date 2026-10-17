"""Read-only lookup of a verified A&T Trucky provider identity.

Never accept a claimed Trucky account ID as proof of ownership.
"""
async def get_verified_trucky_account_id(pool, discord_user_id):
    if type(discord_user_id) is not int or discord_user_id <= 0:
        return None
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT l.provider_account_id
                 FROM unified_provider_links l
                 JOIN unified_driver_profiles p
                   ON p.discord_user_id=l.discord_user_id
                WHERE l.provider='trucky'
                  AND l.discord_user_id=$1
                  AND l.verified_at IS NOT NULL
                  AND p.status='active'""",
            discord_user_id,
        )
    if len(rows) != 1:
        return None
    account_id = rows[0]["provider_account_id"]
    if not isinstance(account_id, str) or not account_id.isdecimal() or int(account_id) <= 0:
        return None
    return account_id


async def review_job_for_registered_driver(pool, job, discord_user_id):
    """Return only an unawardable candidate; no DB writes or role changes."""
    from trucky_verified_job_candidate_v2 import normalize_verified_trucky_job
    account_id = await get_verified_trucky_account_id(pool, discord_user_id)
    if account_id is None:
        return None
    return normalize_verified_trucky_job(
        job, verified_provider_account_id=account_id,
        discord_user_id=discord_user_id,
    )
