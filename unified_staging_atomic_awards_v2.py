"""Staging-only transactional mileage awards, isolated from live driver_progress.

This is a database proof of atomic provider idempotency and fractional carry.
It is NOT a live TrucksBook/Trucky ingestion worker or cross-provider matcher.
"""
from decimal import Decimal
from unified_fractional_mileage_v2 import accumulate_miles

SCHEMA = """
CREATE TABLE IF NOT EXISTS unified_staging_award_carry (
 discord_user_id BIGINT PRIMARY KEY REFERENCES unified_driver_profiles(discord_user_id),
 whole_miles BIGINT NOT NULL DEFAULT 0 CHECK(whole_miles>=0),
 fractional_miles NUMERIC(20,10) NOT NULL DEFAULT 0
    CHECK(fractional_miles>=0 AND fractional_miles<1)
);
CREATE TABLE IF NOT EXISTS unified_staging_award_jobs (
 provider TEXT NOT NULL CHECK(provider IN ('trucksbook','trucky')),
 provider_job_id TEXT NOT NULL,
 discord_user_id BIGINT NOT NULL REFERENCES unified_driver_profiles(discord_user_id),
 miles NUMERIC(20,10) NOT NULL CHECK(miles>0),
 PRIMARY KEY(provider,provider_job_id),
 FOREIGN KEY(provider,provider_job_id)
 REFERENCES unified_provider_job_events(provider,provider_job_id)
);
"""

async def install_staging_awards(pool):
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)


async def award_staging_real_miles(pool, provider, job_id, discord_user_id, miles):
    """Award once by provider ID; no production writes or cross-provider promise.

    Call only after trusted reconciliation has independently marked a job
    creditable. A second provider's matching job still needs canonical
    deduplication before this function may be used.
    """
    if provider not in ("trucksbook", "trucky"):
        raise ValueError("Unsupported provider")
    if not isinstance(job_id, str) or not job_id:
        raise ValueError("Invalid provider job ID")
    if type(discord_user_id) is not int or discord_user_id <= 0:
        raise ValueError("Invalid Discord driver")
    # Validation before entering the transaction prevents invalid carry writes.
    accumulate_miles("0", miles)
    async with pool.acquire() as conn:
        async with conn.transaction():
            event = await conn.fetchrow("""
                SELECT discord_user_id,real_miles,race_miles
                FROM unified_provider_job_events
                WHERE provider=$1 AND provider_job_id=$2 FOR UPDATE
            """, provider, job_id)
            if event is None or event["discord_user_id"] != discord_user_id:
                raise ValueError("Provider job missing or driver mismatch")
            if Decimal(str(event["real_miles"])) != Decimal(str(miles)) or Decimal(str(event["race_miles"])) != 0:
                raise ValueError("Staged award differs from recorded real job")
            await conn.execute("""
                INSERT INTO unified_staging_award_carry(discord_user_id)
                VALUES($1) ON CONFLICT(discord_user_id) DO NOTHING
            """, discord_user_id)
            carry = await conn.fetchrow("""
                SELECT whole_miles,fractional_miles
                FROM unified_staging_award_carry
                WHERE discord_user_id=$1 FOR UPDATE
            """, discord_user_id)
            inserted = await conn.fetchval("""
                INSERT INTO unified_staging_award_jobs
                    (provider,provider_job_id,discord_user_id,miles)
                VALUES($1,$2,$3,$4)
                ON CONFLICT(provider,provider_job_id) DO NOTHING
                RETURNING provider_job_id
            """, provider, job_id, discord_user_id, Decimal(str(miles)))
            if inserted is None:
                return {"status":"duplicate_provider_job", "whole_miles":carry["whole_miles"],
                        "fractional_miles":str(carry["fractional_miles"])}
            whole, remainder = accumulate_miles(carry["fractional_miles"], miles)
            await conn.execute("""
                UPDATE unified_staging_award_carry
                SET whole_miles=whole_miles+$2,fractional_miles=$3
                WHERE discord_user_id=$1
            """, discord_user_id, whole, remainder)
            return {"status":"staging_awarded", "credited_whole_miles":whole,
                    "fractional_miles":str(remainder)}
