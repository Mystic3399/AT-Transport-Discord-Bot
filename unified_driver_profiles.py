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

CREATE TABLE IF NOT EXISTS unified_registration_sessions (
    discord_user_id BIGINT PRIMARY KEY REFERENCES unified_driver_profiles(discord_user_id),
    verification_channel_id BIGINT UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','awaiting_review','approved','cancelled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS unified_provider_claims (
    discord_user_id BIGINT NOT NULL REFERENCES unified_driver_profiles(discord_user_id),
    provider TEXT NOT NULL CHECK (provider IN ('trucksbook','trucky')),
    provider_account_id TEXT NOT NULL,
    provider_display_name TEXT,
    membership_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    ownership_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','approved','rejected')),
    reviewed_by BIGINT,
    reviewed_at TIMESTAMPTZ,
    PRIMARY KEY (discord_user_id,provider),
    UNIQUE (provider,provider_account_id)
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
            # Reject conflicting pre-existing provider ownership rather than
            # silently treating a partial migration as successful.
            conflicts = await conn.fetchval("""
                SELECT COUNT(*) FROM driver_links legacy
                JOIN unified_provider_links v2
                  ON v2.provider='trucksbook'
                 AND v2.provider_account_id=legacy.trucksbook_user_id::text
                WHERE legacy.trucksbook_user_id IS NOT NULL
                  AND v2.discord_user_id<>legacy.discord_user_id
            """)
            if conflicts:
                raise ValueError("Conflicting TrucksBook ownership during V2 backfill")

async def has_verified_provider(pool, discord_user_id):
    async with pool.acquire() as conn:
        return bool(await conn.fetchval("""
            SELECT EXISTS(
                SELECT 1 FROM unified_provider_links
                WHERE discord_user_id=$1 AND verified_at IS NOT NULL
            )
        """, discord_user_id))

async def begin_registration(pool, discord_user_id, channel_id):
    """Create a pending identity and registration session; never touch mileage."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("""
                INSERT INTO unified_driver_profiles(discord_user_id)
                VALUES($1) ON CONFLICT(discord_user_id) DO NOTHING
            """, discord_user_id)
            await conn.execute("""
                INSERT INTO unified_registration_sessions(discord_user_id,verification_channel_id)
                VALUES($1,$2)
                ON CONFLICT(discord_user_id) DO UPDATE SET
                    verification_channel_id=EXCLUDED.verification_channel_id,
                    updated_at=NOW()
            """, discord_user_id, channel_id)

async def submit_provider_claim(pool, discord_user_id, provider, account_id, display_name=None):
    """Record a claim, NOT verification or activation."""
    if provider not in ("trucksbook", "trucky"):
        raise ValueError("Unsupported tracking provider")
    if not isinstance(account_id,str) or not account_id.isdigit() or not (1 <= len(account_id) <= 30):
        raise ValueError("Provider account ID must be numeric")
    async with pool.acquire() as conn:
        async with conn.transaction():
            if not await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM unified_registration_sessions WHERE discord_user_id=$1)",
                discord_user_id,
            ):
                raise ValueError("Start /register first")
            await conn.execute("""
                INSERT INTO unified_provider_claims
                    (discord_user_id,provider,provider_account_id,provider_display_name)
                VALUES($1,$2,$3,$4)
                ON CONFLICT(discord_user_id,provider) DO UPDATE SET
                    provider_account_id=EXCLUDED.provider_account_id,
                    provider_display_name=EXCLUDED.provider_display_name,
                    membership_confirmed=FALSE,ownership_confirmed=FALSE,
                    status='pending',reviewed_by=NULL,reviewed_at=NULL
            """, discord_user_id, provider, account_id, display_name)

async def activate_if_verified(pool, discord_user_id):
    """Only verified provider links may activate a profile; no mileage writes."""
    async with pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE unified_driver_profiles p
            SET status='active',activated_at=COALESCE(activated_at,NOW())
            WHERE p.discord_user_id=$1 AND EXISTS(
                SELECT 1 FROM unified_provider_links l
                WHERE l.discord_user_id=p.discord_user_id AND l.verified_at IS NOT NULL
            )
        """, discord_user_id)
        return result == "UPDATE 1"

async def get_pending_claim(pool, discord_user_id, provider):
    if provider not in ("trucksbook", "trucky"):
        raise ValueError("Unsupported tracking provider")
    async with pool.acquire() as conn:
        return await conn.fetchrow("""
            SELECT provider_account_id,provider_display_name,status
            FROM unified_provider_claims
            WHERE discord_user_id=$1 AND provider=$2
        """, discord_user_id, provider)

async def approve_provider_claim(pool, discord_user_id, provider, reviewer_id,
                                 membership_confirmed, ownership_confirmed):
    """Atomic management approval. Never auto-approve from a roster lookup alone."""
    if provider not in ("trucksbook", "trucky"):
        raise ValueError("Unsupported tracking provider")
    if reviewer_id == discord_user_id:
        raise ValueError("You cannot approve your own tracking account")
    if not membership_confirmed or not ownership_confirmed:
        raise ValueError("Membership and account ownership must both be verified")
    async with pool.acquire() as conn:
        async with conn.transaction():
            claim = await conn.fetchrow("""
                SELECT provider_account_id,provider_display_name,status
                FROM unified_provider_claims
                WHERE discord_user_id=$1 AND provider=$2 FOR UPDATE
            """, discord_user_id, provider)
            if claim is None or claim["status"] != "pending":
                raise ValueError("No pending claim for that provider")
            existing = await conn.fetchrow("""
                SELECT provider_account_id FROM unified_provider_links
                WHERE discord_user_id=$1 AND provider=$2 FOR UPDATE
            """, discord_user_id, provider)
            if existing and existing["provider_account_id"] != claim["provider_account_id"]:
                raise ValueError("Provider already linked to a different account; manual review required")
            await conn.execute("""
                INSERT INTO unified_provider_links
                    (provider,provider_account_id,discord_user_id,
                     provider_display_name,verified_at,verified_by)
                VALUES($1,$2,$3,$4,NOW(),$5)
                ON CONFLICT(provider,provider_account_id) DO UPDATE SET
                    provider_display_name=EXCLUDED.provider_display_name
                WHERE unified_provider_links.discord_user_id=EXCLUDED.discord_user_id
            """, provider,claim["provider_account_id"],discord_user_id,
                claim["provider_display_name"],reviewer_id)
            linked = await conn.fetchval("""
                SELECT EXISTS(SELECT 1 FROM unified_provider_links
                WHERE provider=$1 AND provider_account_id=$2
                  AND discord_user_id=$3 AND verified_at IS NOT NULL)
            """,provider,claim["provider_account_id"],discord_user_id)
            if not linked:
                raise ValueError("Account is already owned by another driver")
            await conn.execute("""
                UPDATE unified_provider_claims SET status='approved',
                    membership_confirmed=TRUE,ownership_confirmed=TRUE,
                    reviewed_by=$3,reviewed_at=NOW()
                WHERE discord_user_id=$1 AND provider=$2
            """,discord_user_id,provider,reviewer_id)
            await conn.execute("""
                UPDATE unified_driver_profiles
                SET status='active',activated_at=COALESCE(activated_at,NOW())
                WHERE discord_user_id=$1
            """,discord_user_id)
            await conn.execute("""
                UPDATE unified_registration_sessions
                SET status='approved',updated_at=NOW()
                WHERE discord_user_id=$1
            """,discord_user_id)
            return claim["provider_account_id"]
