"""Isolated V2 staging mileage decisions. Never updates legacy mileage."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS unified_staging_mileage_ledger (
 provider TEXT NOT NULL,
 provider_job_id TEXT NOT NULL,
 discord_user_id BIGINT NOT NULL REFERENCES unified_driver_profiles(discord_user_id),
 real_miles NUMERIC(12,2) NOT NULL DEFAULT 0,
 race_miles NUMERIC(12,2) NOT NULL DEFAULT 0,
 status TEXT NOT NULL CHECK(status IN ('review','credited','duplicate')),
 reviewed_by BIGINT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 PRIMARY KEY(provider,provider_job_id),
 FOREIGN KEY(provider,provider_job_id)
 REFERENCES unified_provider_job_events(provider,provider_job_id)
);
"""

async def install_staging_ledger(pool):
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)


async def stage_decision(pool, provider, job_id, decision, reviewer_id=None):
    """Immutable staging decision; no changes to driver_progress or Discord."""
    if provider not in ("trucky", "trucksbook") or decision not in ("review", "credited", "duplicate"):
        raise ValueError("Invalid provider or decision")
    if not isinstance(job_id, str) or not job_id:
        raise ValueError("Missing job ID")
    if decision != "review" and (type(reviewer_id) is not int or reviewer_id <= 0):
        raise ValueError("Reviewer identity required")
    async with pool.acquire() as conn:
        async with conn.transaction():
            event = await conn.fetchrow("""
                SELECT discord_user_id,real_miles,race_miles
                FROM unified_provider_job_events
                WHERE provider=$1 AND provider_job_id=$2 FOR UPDATE
            """, provider, job_id)
            if event is None:
                raise ValueError("Unknown job")
            existing = await conn.fetchrow("""
                SELECT status,reviewed_by FROM unified_staging_mileage_ledger
                WHERE provider=$1 AND provider_job_id=$2 FOR UPDATE
            """, provider, job_id)
            if existing is not None:
                if existing["status"] != decision or existing["reviewed_by"] != reviewer_id:
                    raise ValueError("Conflicting ledger decision requires audited correction")
                return False
            await conn.execute("""
                INSERT INTO unified_staging_mileage_ledger
                (provider,provider_job_id,discord_user_id,real_miles,race_miles,status,reviewed_by)
                VALUES($1,$2,$3,$4,$5,$6,$7)
            """, provider, job_id, event["discord_user_id"],
                 event["real_miles"] if decision == "credited" else 0,
                 event["race_miles"] if decision == "credited" else 0,
                 decision, reviewer_id)
            return True


async def staging_totals(pool, discord_user_id):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT COALESCE(SUM(real_miles),0) AS real_miles,
                   COALESCE(SUM(race_miles),0) AS race_miles
            FROM unified_staging_mileage_ledger
            WHERE discord_user_id=$1 AND status='credited'
        """, discord_user_id)
        return dict(row)

async def auto_credit_unmatched_job(pool, provider, job_id):
    """Automatically credit only when complete metadata rules out a cross-provider match.

    Ambiguous jobs stay uncredited for review; never touch legacy driver totals.
    """
    from unified_job_review_v2 import suggest_cross_provider_matches
    if provider not in ("trucky", "trucksbook") or not isinstance(job_id, str) or not job_id:
        raise ValueError("Invalid job reference")
    async with pool.acquire() as conn:
        event = await conn.fetchrow("""
            SELECT discord_user_id,delivered_at,source_payload
            FROM unified_provider_job_events
            WHERE provider=$1 AND provider_job_id=$2
        """, provider, job_id)
    if event is None:
        raise ValueError("Unknown provider job")
    import json
    payload = event["source_payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict) or not payload.get("job_metadata") or event["delivered_at"] is None:
        return await stage_decision(pool, provider, job_id, "review")
    candidates = await suggest_cross_provider_matches(pool, provider, job_id)
    # A matching event requires human reconciliation; no duplicate credit.
    if candidates:
        return await stage_decision(pool, provider, job_id, "review")
    # A no-match decision is not yet safe to auto-credit: the other provider
    # may deliver the same job later. Leave pending for reconciliation.
    return await stage_decision(pool, provider, job_id, "review")
