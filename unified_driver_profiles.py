"""Additive, non-destructive unified A&T driver identity schema.

This module intentionally does not alter legacy TrucksBook mileage, ranks, jobs,
or driver_links. Run only after a reviewed backup and staged verification.
"""
SCHEMA = """
CREATE TABLE IF NOT EXISTS unified_driver_profiles (
    discord_user_id BIGINT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'active', 'suspended'))
);
CREATE TABLE IF NOT EXISTS unified_provider_links (
    provider TEXT NOT NULL CHECK (provider IN ('trucksbook','trucky')),
    provider_account_id TEXT NOT NULL,
    discord_user_id BIGINT NOT NULL REFERENCES unified_driver_profiles(discord_user_id),
    provider_display_name TEXT,
    verified_at TIMESTAMPTZ,
    verified_by BIGINT,
    PRIMARY KEY (provider, provider_account_id),
    UNIQUE (discord_user_id, provider)
);
CREATE TABLE IF NOT EXISTS unified_provider_job_events (
    provider TEXT NOT NULL CHECK (provider IN ('trucksbook','trucky')),
    provider_job_id TEXT NOT NULL,
    discord_user_id BIGINT NOT NULL REFERENCES unified_driver_profiles(discord_user_id),
    real_miles NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (real_miles >= 0),
    race_miles NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (race_miles >= 0),
    delivered_at TIMESTAMPTZ,
    source_payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (provider, provider_job_id)
);
CREATE TABLE IF NOT EXISTS unified_job_matches (
    provider TEXT NOT NULL,
    provider_job_id TEXT NOT NULL,
    canonical_job_key TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'unreviewed'
        CHECK (review_status IN ('unreviewed','confirmed','rejected')),
    PRIMARY KEY (provider, provider_job_id),
    FOREIGN KEY (provider, provider_job_id)
        REFERENCES unified_provider_job_events(provider, provider_job_id)
);
"""

async def install_schema(pool):
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)

async def backfill_trucksbook_profiles(pool):
    """Idempotent identity backfill; no legacy table changes."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("""
                INSERT INTO unified_driver_profiles(discord_user_id,status,activated_at)
                SELECT discord_user_id,'active',NOW() FROM driver_links
                ON CONFLICT(discord_user_id) DO NOTHING
            """)
            await conn.execute("""
                INSERT INTO unified_provider_links
                    (provider,provider_account_id,discord_user_id,provider_display_name,verified_at)
                SELECT 'trucksbook',trucksbook_user_id::text,discord_user_id,trucksbook_name,linked_at
                FROM driver_links WHERE trucksbook_user_id IS NOT NULL
                ON CONFLICT(provider,provider_account_id) DO NOTHING
            """)

async def has_verified_provider(pool, discord_user_id):
    async with pool.acquire() as conn:
        return bool(await conn.fetchval("""
            SELECT EXISTS(
                SELECT 1 FROM unified_provider_links
                WHERE discord_user_id=$1 AND verified_at IS NOT NULL
            )
        """, discord_user_id))
