"""Additive, non-destructive unified A&T driver identity schema.

This module intentionally does not alter legacy TrucksBook mileage, ranks, jobs,
or driver_links. Run only after a reviewed backup and staged verification.
"""


class TruckyIdentityVerificationError(ValueError):
    """Safe machine-readable failure category; never includes identifiers."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


SCHEMA = """
CREATE TABLE IF NOT EXISTS unified_driver_profiles (
 discord_user_id BIGINT PRIMARY KEY,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 activated_at TIMESTAMPTZ,
 status TEXT NOT NULL DEFAULT 'pending'
 CHECK (status IN ('pending','active','suspended'))
);
CREATE TABLE IF NOT EXISTS unified_steam_links (
 discord_user_id BIGINT PRIMARY KEY REFERENCES unified_driver_profiles(discord_user_id),
 steam_id64 TEXT NOT NULL UNIQUE CHECK (steam_id64 ~ '^7656119[0-9]{10}$'),
 verified_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS unified_steam_auth_states (
 state_hash TEXT PRIMARY KEY,
 discord_user_id BIGINT NOT NULL REFERENCES unified_driver_profiles(discord_user_id),
 expected_return_to TEXT NOT NULL,
 expires_at TIMESTAMPTZ NOT NULL,
 consumed_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS unified_steam_openid_nonces (
 nonce TEXT PRIMARY KEY,
 consumed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
 CHECK(status IN ('pending','awaiting_review','approved','cancelled')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS unified_provider_claims (
 discord_user_id BIGINT NOT NULL REFERENCES unified_driver_profiles(discord_user_id),
 provider TEXT NOT NULL CHECK(provider IN ('trucksbook','trucky')),
 provider_account_id TEXT NOT NULL,
 provider_display_name TEXT,
 membership_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
 ownership_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
 submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 expires_at TIMESTAMPTZ,
 status TEXT NOT NULL DEFAULT 'pending'
 CHECK(status IN ('pending','approved','rejected')),
 reviewed_by BIGINT,
 reviewed_at TIMESTAMPTZ,
 ownership_evidence_kind TEXT,
 ownership_evidence_sha256 TEXT CHECK(
   ownership_evidence_sha256 IS NULL OR ownership_evidence_sha256 ~ '^[0-9a-f]{64}$'),
 PRIMARY KEY(discord_user_id,provider),
 UNIQUE(provider,provider_account_id)
);
ALTER TABLE unified_provider_claims
 ADD COLUMN IF NOT EXISTS ownership_evidence_kind TEXT;
ALTER TABLE unified_provider_claims
 ADD COLUMN IF NOT EXISTS ownership_evidence_sha256 TEXT;
DO $$ BEGIN
 IF NOT EXISTS (
   SELECT 1 FROM pg_constraint
   WHERE conname='unified_provider_claims_evidence_sha256_check'
     AND conrelid='unified_provider_claims'::regclass
 ) THEN
   ALTER TABLE unified_provider_claims
     ADD CONSTRAINT unified_provider_claims_evidence_sha256_check
     CHECK(ownership_evidence_sha256 IS NULL OR ownership_evidence_sha256 ~ '^[0-9a-f]{64}$');
 END IF;
END $$;
ALTER TABLE unified_provider_claims
 ADD COLUMN IF NOT EXISTS submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE unified_provider_claims
 ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
ALTER TABLE unified_provider_claims ALTER COLUMN expires_at DROP NOT NULL;
ALTER TABLE unified_provider_claims ALTER COLUMN expires_at DROP DEFAULT;
UPDATE unified_provider_claims SET expires_at=NULL WHERE status='pending' AND expires_at IS NOT NULL;
CREATE TABLE IF NOT EXISTS unified_provider_job_events (
 provider TEXT NOT NULL CHECK(provider IN ('trucksbook','trucky')),
 provider_job_id TEXT NOT NULL,
 discord_user_id BIGINT NOT NULL REFERENCES unified_driver_profiles(discord_user_id),
 provider_account_id TEXT,
 real_miles NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK(real_miles >= 0),
 race_miles NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK(race_miles >= 0),
 delivered_at TIMESTAMPTZ,
 source_payload JSONB,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 PRIMARY KEY (provider, provider_job_id)
);
ALTER TABLE unified_provider_job_events
 ADD COLUMN IF NOT EXISTS provider_account_id TEXT;
CREATE TABLE IF NOT EXISTS unified_job_reconciliation_cases (
 case_id BIGSERIAL PRIMARY KEY,
 trucky_job_id TEXT NOT NULL,
 trucksbook_job_id TEXT NOT NULL,
 discord_user_id BIGINT REFERENCES unified_driver_profiles(discord_user_id),
 status TEXT NOT NULL DEFAULT 'potential_duplicate'
  CHECK(status IN ('potential_duplicate','verified_independent','confirmed_duplicate')),
 opened_by BIGINT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 reviewed_at TIMESTAMPTZ,
 UNIQUE(trucky_job_id, trucksbook_job_id)
);
CREATE TABLE IF NOT EXISTS unified_job_reconciliation_audit (
 audit_id BIGSERIAL PRIMARY KEY,
 case_id BIGINT NOT NULL REFERENCES unified_job_reconciliation_cases(case_id),
 decision TEXT NOT NULL CHECK(decision IN (
  'potential_duplicate','verified_independent','confirmed_duplicate')),
 reviewer_discord_id BIGINT NOT NULL,
 evidence_kind TEXT,
 evidence_sha256 TEXT CHECK(evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$'),
 discord_user_id BIGINT REFERENCES unified_driver_profiles(discord_user_id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS unified_job_matches (
 provider TEXT NOT NULL,
 provider_job_id TEXT NOT NULL,
 canonical_job_key TEXT NOT NULL,
 review_status TEXT NOT NULL DEFAULT 'unreviewed'
 CHECK(review_status IN ('unreviewed','confirmed','rejected')),
 PRIMARY KEY(provider,provider_job_id),
 FOREIGN KEY(provider,provider_job_id)
 REFERENCES unified_provider_job_events(provider,provider_job_id)
);
CREATE TABLE IF NOT EXISTS unified_trucky_webhook_receipts (
 receipt_id BIGSERIAL PRIMARY KEY,
 payload_sha256 TEXT NOT NULL UNIQUE CHECK(payload_sha256 ~ '^[0-9a-f]{64}$'),
 event_type TEXT NOT NULL,
 candidate_job_id TEXT,
 received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 review_status TEXT NOT NULL DEFAULT 'needs_api_verification'
 CHECK(review_status IN ('needs_api_verification','verified','rejected'))
);
"""
async def install_schema(pool):
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)

async def record_trucky_webhook_receipt(pool, payload_sha256, event_type, candidate_job_id=None):
    """Persist minimal HMAC-authenticated receipt metadata for later API review.

    This is deliberately not an award path: it stores no driver identity,
    distance, raw payload, progression total, or role change.
    """
    import re
    if not isinstance(payload_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", payload_sha256):
        raise ValueError("Invalid payload digest")
    if not isinstance(event_type, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", event_type):
        raise ValueError("Invalid Trucky event type")
    if candidate_job_id is not None:
        if type(candidate_job_id) not in (str, int) or not str(candidate_job_id).isdecimal() or int(candidate_job_id) <= 0:
            raise ValueError("Invalid candidate Trucky job ID")
        candidate_job_id = str(candidate_job_id)
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO unified_trucky_webhook_receipts
                (payload_sha256,event_type,candidate_job_id)
            VALUES($1,$2,$3)
            ON CONFLICT(payload_sha256) DO NOTHING
            RETURNING receipt_id
        """, payload_sha256, event_type, candidate_job_id)
        if row:
            return row["receipt_id"]
        # Replays return the existing audit row so transient membership/API
        # failures can be retried. Link approval remains idempotent in its own
        # transaction and rejects expired or already-consumed claims.
        return await conn.fetchval(
            "SELECT receipt_id FROM unified_trucky_webhook_receipts WHERE payload_sha256=$1",
            payload_sha256,
        )

async def list_trucky_receipt_audit(pool, candidate_job_id, *, limit=20):
    """Return minimal HMAC-verified receipt metadata for a job ID.

    Payload digests, bodies, driver identities and mileage are deliberately
    omitted so this read-only audit can be reported safely.
    """
    if (type(candidate_job_id) not in (str, int)
            or not str(candidate_job_id).isdecimal()
            or int(candidate_job_id) <= 0):
        raise ValueError("Invalid candidate Trucky job ID")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Invalid receipt limit")
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT receipt_id,event_type,received_at,review_status
              FROM unified_trucky_webhook_receipts
             WHERE candidate_job_id=$1
             ORDER BY received_at DESC
             LIMIT $2
        """, str(candidate_job_id), limit)
    return [dict(row) for row in rows]


async def mark_staging_trucky_receipts_verified_after_api_audit(
    pool, candidate_job_id, *, hmac_receipt_verified,
    authenticated_api_job_verified, driver_identity_verified, active_member,
):
    """Advance only staging receipt review state after independent verification.

    This operation never changes mileage, registrations, achievements, or roles.
    All evidence gates must be literal True, and the isolated-staging flag is
    mandatory. Replays are idempotent; rejected receipts remain rejected.
    """
    import os
    if os.getenv("AT_V2_ISOLATED_STAGING") != "YES":
        raise RuntimeError("Staging receipt review transition is disabled")
    if (type(candidate_job_id) not in (str, int)
            or not str(candidate_job_id).isdecimal()
            or int(candidate_job_id) <= 0):
        raise ValueError("Invalid candidate Trucky job ID")
    candidate_job_id = str(candidate_job_id)
    if not all(value is True for value in (
        hmac_receipt_verified, authenticated_api_job_verified,
        driver_identity_verified, active_member,
    )):
        return {"status": "evidence_incomplete", "updated_count": 0}
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch("""
                SELECT receipt_id,review_status
                  FROM unified_trucky_webhook_receipts
                 WHERE candidate_job_id=$1 AND event_type='job_completed'
                 ORDER BY receipt_id FOR UPDATE
            """, candidate_job_id)
            if not rows:
                return {"status": "receipt_missing", "updated_count": 0}
            states = {row["review_status"] for row in rows}
            if "rejected" in states:
                return {"status": "receipt_rejected", "updated_count": 0}
            if states == {"verified"}:
                return {"status": "already_verified", "updated_count": 0}
            result = await conn.execute("""
                UPDATE unified_trucky_webhook_receipts
                   SET review_status='verified'
                 WHERE candidate_job_id=$1 AND event_type='job_completed'
                   AND review_status='needs_api_verification'
            """, candidate_job_id)
            updated = int(result.rsplit(" ", 1)[-1])
    return {
        "status": "verified" if updated or states == {"verified"} else "no_change",
        "updated_count": updated,
    }

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
            if provider == "trucky" and not await conn.fetchval(
                """SELECT EXISTS(SELECT 1 FROM unified_steam_links
                   WHERE discord_user_id=$1 AND verified_at IS NOT NULL)""",
                discord_user_id,
            ):
                raise ValueError("Verify Steam before submitting a Trucky claim")
            await conn.execute("""
                INSERT INTO unified_provider_claims
                    (discord_user_id,provider,provider_account_id,provider_display_name)
                VALUES($1,$2,$3,$4)
                ON CONFLICT(discord_user_id,provider) DO UPDATE SET
                    provider_account_id=EXCLUDED.provider_account_id,
                    provider_display_name=EXCLUDED.provider_display_name,
                    membership_confirmed=FALSE,ownership_confirmed=FALSE,
                    submitted_at=NOW(),expires_at=NULL,
                    status='pending',reviewed_by=NULL,reviewed_at=NULL
            """, discord_user_id, provider, account_id, display_name)


async def submit_trucksbook_pending_claim(pool, discord_user_id, account_id):
    """Record an unverified TrucksBook ID claim for an existing Steam-verified driver.

    A numeric ID or display name is never ownership evidence. This operation
    stores no provider link and cannot activate the profile.
    """
    if (not isinstance(account_id, str) or not account_id.isascii()
            or not account_id.isdecimal() or not 1 <= len(account_id) <= 30):
        raise ValueError("TrucksBook account ID must be numeric")
    async with pool.acquire() as conn:
        async with conn.transaction():
            started = await conn.fetchval("""
                SELECT EXISTS(SELECT 1 FROM unified_registration_sessions
                              WHERE discord_user_id=$1)
            """, discord_user_id)
            if not started:
                raise ValueError("Start /register first")
            steam_verified = await conn.fetchval("""
                SELECT EXISTS(SELECT 1 FROM unified_steam_links
                              WHERE discord_user_id=$1 AND verified_at IS NOT NULL)
            """, discord_user_id)
            if not steam_verified:
                raise ValueError("Verify Steam before submitting a TrucksBook claim")
            current = await conn.fetchrow("""
                SELECT provider_account_id,status FROM unified_provider_claims
                WHERE discord_user_id=$1 AND provider='trucksbook' FOR UPDATE
            """, discord_user_id)
            if current and current["status"] == "approved":
                raise ValueError("A TrucksBook account is already linked; contact management to change it")
            # Reject account IDs already claimed or linked by another profile.
            owner = await conn.fetchval("""
                SELECT EXISTS(
                  SELECT 1 FROM unified_provider_claims
                  WHERE provider='trucksbook' AND provider_account_id=$1
                    AND discord_user_id<>$2 AND status IN ('pending','approved')
                  UNION ALL
                  SELECT 1 FROM unified_provider_links
                  WHERE provider='trucksbook' AND provider_account_id=$1
                    AND discord_user_id<>$2
                )
            """, account_id, discord_user_id)
            if owner:
                raise ValueError("That TrucksBook account is already claimed by another driver")
            await conn.execute("""
                INSERT INTO unified_provider_claims
                  (discord_user_id,provider,provider_account_id,provider_display_name,
                   membership_confirmed,ownership_confirmed,status,reviewed_by,reviewed_at,
                   ownership_evidence_kind,ownership_evidence_sha256,submitted_at,expires_at)
                VALUES($1,'trucksbook',$2,NULL,FALSE,FALSE,'pending',NULL,NULL,NULL,NULL,NOW(),NULL)
                ON CONFLICT(discord_user_id,provider) DO UPDATE SET
                  provider_account_id=EXCLUDED.provider_account_id,
                  provider_display_name=NULL,membership_confirmed=FALSE,
                  ownership_confirmed=FALSE,status='pending',reviewed_by=NULL,
                  reviewed_at=NULL,ownership_evidence_kind=NULL,
                  ownership_evidence_sha256=NULL,submitted_at=NOW(),expires_at=NULL
            """, discord_user_id, account_id)
    return {"status": "pending_verification", "provider": "trucksbook"}


async def approve_trucksbook_claim_after_witness(
    pool, discord_user_id, reviewer_id, observed_steam_id64,
    observed_trucksbook_id, evidence_sha256, *, membership_confirmed,
):
    """Retired unsupported Steam-SSO review; never writes or acquires a database.

    TrucksBook's Steam step is initial registration, not normal account login.
    Email-session control and A&T Steam OpenID are separate evidence; neither
    a reviewer-supplied Steam ID nor an evidence digest proves their linkage.
    Retain the entry point so stale callers fail closed without changing data.
    """
    raise ValueError(
        "TrucksBook ownership verification is pending provider-supported evidence. "
        "Normal TrucksBook login uses email/password; the former witnessed "
        "Steam-login approval is disabled. No account or mileage was changed."
    )


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


async def get_trucky_identity_link_diagnostics(pool, discord_user_id):
    """Read the caller's link-state flags without returning provider IDs/names.

    This deliberately does not mutate registration data and cannot establish
    ownership; it is a staging-only diagnostic for comparing stored link state
    with a later signed Trucky event's redacted verification reason.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT
                EXISTS(
                    SELECT 1 FROM unified_steam_links
                    WHERE discord_user_id=$1 AND verified_at IS NOT NULL
                ) AS steam_verified,
                (SELECT status FROM unified_provider_claims
                    WHERE discord_user_id=$1 AND provider='trucky'
                ) AS trucky_claim_status,
                EXISTS(
                    SELECT 1 FROM unified_provider_links
                    WHERE discord_user_id=$1 AND provider='trucky'
                      AND verified_at IS NOT NULL
                ) AS trucky_link_verified,
                CASE WHEN EXISTS(
                    SELECT 1 FROM unified_provider_claims
                    WHERE discord_user_id=$1 AND provider='trucky'
                ) AND EXISTS(
                    SELECT 1 FROM unified_provider_links
                    WHERE discord_user_id=$1 AND provider='trucky'
                      AND verified_at IS NOT NULL
                ) THEN EXISTS(
                    SELECT 1 FROM unified_provider_claims c
                    JOIN unified_provider_links l
                      ON l.provider='trucky'
                     AND l.provider_account_id=c.provider_account_id
                     AND l.discord_user_id=c.discord_user_id
                    WHERE c.discord_user_id=$1 AND c.provider='trucky'
                      AND l.verified_at IS NOT NULL
                ) ELSE NULL END AS claim_link_match
        """, discord_user_id)
    return {
        "steam_verified": bool(row["steam_verified"]),
        "trucky_claim_status": row["trucky_claim_status"],
        "trucky_link_verified": bool(row["trucky_link_verified"]),
        "claim_link_match": row["claim_link_match"],
    }

async def verify_trucky_event_against_verified_links(pool, account_id, provider_steam_id):
    """Read-only comparison of signed event identity with an existing verified profile.

    This function performs no claim/link, mileage, role, or achievement writes.
    The caller must validate the Trucky signature and confirm active membership.
    """
    import steam_openid_v2

    if not isinstance(account_id, str) or not account_id.isdecimal():
        return {"status": "invalid_account_id", "discord_user_id": None}
    if not isinstance(provider_steam_id, str) or not provider_steam_id.isdecimal():
        return {"status": "provider_steam_evidence_missing_or_invalid", "discord_user_id": None}

    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT c.discord_user_id, c.provider_account_id, c.status,
                   s.steam_id64,
                   l.provider_account_id AS linked_account_id,
                   l.verified_at AS linked_verified_at,
                   s.verified_at AS steam_verified_at
            FROM unified_provider_claims c
            LEFT JOIN unified_steam_links s
              ON s.discord_user_id=c.discord_user_id
            LEFT JOIN unified_provider_links l
              ON l.discord_user_id=c.discord_user_id AND l.provider='trucky'
            WHERE c.provider='trucky' AND c.provider_account_id=$1
            LIMIT 2
        """, account_id)

    if row is None:
        return {"status": "claim_missing", "discord_user_id": None}
    if row["status"] != "approved":
        return {"status": "claim_missing_or_inactive", "discord_user_id": None}
    if (row["linked_account_id"] != account_id
            or row["linked_verified_at"] is None):
        return {"status": "approved_link_inconsistent", "discord_user_id": None}
    if row["steam_id64"] is None or row["steam_verified_at"] is None:
        return {"status": "steam_link_missing", "discord_user_id": None}
    if not steam_openid_v2.steam_ids_match(
        row["steam_id64"], provider_steam_id, trusted_provider_source=True
    ):
        return {"status": "steam_identity_mismatch", "discord_user_id": None}
    return {
        "status": "identity_verified_read_only",
        "discord_user_id": int(row["discord_user_id"]),
    }


async def verify_trucky_account_against_verified_links(pool, account_id):
    """Read-only ownership chain for an authenticated Trucky account ID.

    Company jobs records expose a Trucky driver account ID, not necessarily a
    Steam ID. This check accepts that record only when the account is uniquely
    linked to an approved claim, the claim records both membership and
    ownership confirmation, and the same profile has a verified Steam link.
    The caller must still independently confirm active company membership.
    """
    if not isinstance(account_id, str) or not account_id.isdecimal() or int(account_id) <= 0:
        return {"status": "invalid_account_id"}
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT
                (SELECT COUNT(*) FROM unified_provider_claims
                  WHERE provider='trucky' AND provider_account_id=$1) AS claim_count,
                (SELECT COUNT(*) FROM unified_provider_links
                  WHERE provider='trucky' AND provider_account_id=$1) AS link_count,
                c.discord_user_id, c.status AS claim_status,
                c.membership_confirmed, c.ownership_confirmed,
                l.provider_account_id AS linked_account_id,
                l.verified_at AS provider_verified_at,
                s.verified_at AS steam_verified_at
            FROM unified_provider_claims c
            LEFT JOIN unified_provider_links l
              ON l.discord_user_id=c.discord_user_id AND l.provider='trucky'
            LEFT JOIN unified_steam_links s
              ON s.discord_user_id=c.discord_user_id
            WHERE c.provider='trucky' AND c.provider_account_id=$1
            LIMIT 1
        """, account_id)
    if row is None or row["claim_count"] == 0:
        return {"status": "claim_missing"}
    if row["claim_count"] != 1 or row["link_count"] != 1:
        return {"status": "account_link_ambiguous"}
    if row["claim_status"] != "approved":
        return {"status": "claim_not_approved"}
    if not row["membership_confirmed"]:
        return {"status": "claim_membership_unconfirmed"}
    if not row["ownership_confirmed"]:
        return {"status": "claim_ownership_unconfirmed"}
    if row["linked_account_id"] != account_id or row["provider_verified_at"] is None:
        return {"status": "verified_provider_link_missing_or_mismatched"}
    if row["steam_verified_at"] is None:
        return {"status": "steam_link_unverified"}
    return {"status": "verified_trucky_account_link_matches_steam"}


async def approve_provider_claim(pool, discord_user_id, provider, reviewer_id,
                                 membership_confirmed, ownership_confirmed):
    """Atomic management approval. Never auto-approve from a roster lookup alone."""
    if provider not in ("trucksbook", "trucky"):
        raise ValueError("Unsupported tracking provider")
    if provider == "trucky":
        raise ValueError("Trucky ownership requires a fresh signed job event matching Steam OpenID")
    if provider == "trucksbook":
        raise ValueError("TrucksBook ownership is pending provider-supported verification; the Steam-login flow is disabled")
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
            steam_verified = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM unified_steam_links "
                "WHERE discord_user_id=$1 AND verified_at IS NOT NULL)",
                discord_user_id,
            )
            if not steam_verified:
                raise ValueError("Verify Steam before provider approval")
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

async def bind_verified_steam(pool, discord_user_id, steam_id64, state_hash, nonce):
    """Consume pre-created, short-lived state and nonce and bind verified Steam ID.

    Call ONLY after verify_steam_callback succeeded. This does not grant a driver
    role or approve a tracking provider without verified provider-side Steam IDs.
    """
    if not isinstance(steam_id64, str) or not __import__("re").fullmatch(r"7656119[0-9]{10}", steam_id64):
        raise ValueError("Invalid SteamID64")
    async with pool.acquire() as conn:
        async with conn.transaction():
            state = await conn.fetchrow("""
                SELECT discord_user_id,expires_at,consumed_at
                FROM unified_steam_auth_states WHERE state_hash=$1 FOR UPDATE
            """,state_hash)
            if state is None or state["discord_user_id"] != discord_user_id:
                raise ValueError("Unknown Steam authentication state")
            if state["consumed_at"] is not None:
                raise ValueError("Steam authentication state already used")
            valid = await conn.fetchval("""
                SELECT expires_at > NOW() FROM unified_steam_auth_states
                WHERE state_hash=$1
            """,state_hash)
            if not valid:
                raise ValueError("Steam authentication state expired")
            nonce_result = await conn.execute("""
                INSERT INTO unified_steam_openid_nonces(nonce) VALUES($1)
                ON CONFLICT DO NOTHING
            """,nonce)
            if nonce_result != "INSERT 0 1":
                raise ValueError("Steam OpenID response was already used")
            existing_steam = await conn.fetchval("""
                SELECT steam_id64 FROM unified_steam_links
                WHERE discord_user_id=$1 FOR UPDATE
            """,discord_user_id)
            if existing_steam is not None and existing_steam != steam_id64:
                raise ValueError("Steam account change requires exception review")
            other = await conn.fetchval("""
                SELECT discord_user_id FROM unified_steam_links
                WHERE steam_id64=$1 FOR UPDATE
            """,steam_id64)
            if other is not None and other != discord_user_id:
                raise ValueError("Steam account already linked to another Discord user")
            bind_result = await conn.execute("""
                INSERT INTO unified_steam_links(discord_user_id,steam_id64)
                VALUES($1,$2)
                ON CONFLICT(discord_user_id) DO UPDATE SET
                    verified_at=NOW()
                WHERE unified_steam_links.steam_id64=EXCLUDED.steam_id64
            """,discord_user_id,steam_id64)
            if bind_result not in ("INSERT 0 1", "UPDATE 1"):
                raise ValueError("Steam identity changed during verification")
            await conn.execute("""
                UPDATE unified_steam_auth_states SET consumed_at=NOW()
                WHERE state_hash=$1
            """,state_hash)

async def create_steam_auth_state(pool, discord_user_id, callback_base, ttl_minutes=10):
    """Persist one-time opaque browser state, returning its URL-safe raw token."""
    import secrets
    import hashlib
    from urllib.parse import urlsplit, urlencode, urlunsplit, parse_qsl
    parsed = urlsplit(callback_base)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("HTTPS callback required")
    if ttl_minutes < 1 or ttl_minutes > 15:
        raise ValueError("Invalid Steam login TTL")
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["state"] = token
    expected_return_to = urlunsplit((parsed.scheme,parsed.netloc,parsed.path,urlencode(query),""))
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO unified_steam_auth_states
                (state_hash,discord_user_id,expected_return_to,expires_at)
            VALUES($1,$2,$3,NOW()+($4 * INTERVAL '1 minute'))
        """,token_hash,discord_user_id,expected_return_to,ttl_minutes)
    return expected_return_to

async def auto_approve_trucky_if_steam_matches(
    pool, discord_user_id, account_id, provider_steam_id,
    *, trusted_provider_source=False, membership_confirmed=False,
    provider_job_id=None, payload_sha256=None, completed_at=None,
):
    """Atomically link after fresh signed-event, Steam, and roster evidence.

    This method does not trust a submitted account ID or a member name. Caller
    MUST verify the Trucky HMAC, extract the documented user Steam profile,
    confirm current membership in the authenticated company roster, and pass
    the job receipt identifiers. No mileage is modified here.
    """
    import steam_openid_v2
    import re
    from datetime import datetime, timezone

    if not membership_confirmed or not trusted_provider_source:
        raise TruckyIdentityVerificationError("untrusted_evidence")
    if not isinstance(account_id, str) or not account_id.isdecimal():
        raise TruckyIdentityVerificationError("invalid_account_id")
    if not isinstance(provider_job_id, str) or not provider_job_id.isdecimal():
        raise TruckyIdentityVerificationError("job_evidence_missing")
    if not isinstance(payload_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", payload_sha256):
        raise TruckyIdentityVerificationError("receipt_digest_invalid")
    if not isinstance(completed_at, datetime) or completed_at.tzinfo is None or completed_at.utcoffset() is None:
        raise TruckyIdentityVerificationError("completion_time_invalid")
    completed_at = completed_at.astimezone(timezone.utc)
    async with pool.acquire() as conn:
        async with conn.transaction():
            if discord_user_id is None:
                claim = await conn.fetchrow("""
                    SELECT discord_user_id,provider_account_id,provider_display_name,
                           status,submitted_at,expires_at
                    FROM unified_provider_claims
                    WHERE provider='trucky' AND provider_account_id=$1
                    FOR UPDATE
                """, account_id)
                if claim is None:
                    raise TruckyIdentityVerificationError("claim_missing")
                discord_user_id = claim["discord_user_id"]
            else:
                claim = await conn.fetchrow("""
                    SELECT discord_user_id,provider_account_id,provider_display_name,
                           status,submitted_at,expires_at
                    FROM unified_provider_claims
                    WHERE discord_user_id=$1 AND provider='trucky' FOR UPDATE
                """, discord_user_id)
            if claim is None or claim["status"] not in ("pending", "approved"):
                raise TruckyIdentityVerificationError("claim_missing_or_inactive")
            if claim["provider_account_id"] != account_id:
                raise TruckyIdentityVerificationError("claim_account_mismatch")
            steam = await conn.fetchval("""
                SELECT steam_id64 FROM unified_steam_links
                WHERE discord_user_id=$1 AND verified_at IS NOT NULL
            """, discord_user_id)
            if steam is None:
                raise TruckyIdentityVerificationError("steam_link_missing")
            if not steam_openid_v2.steam_ids_match(
                steam, provider_steam_id, trusted_provider_source
            ):
                raise TruckyIdentityVerificationError("steam_identity_mismatch")
            receipt_exists = await conn.fetchval("""
                SELECT EXISTS(
                    SELECT 1 FROM unified_trucky_webhook_receipts
                    WHERE payload_sha256=$1 AND event_type='job_completed'
                      AND candidate_job_id=$2
                )
            """, payload_sha256, provider_job_id)
            if not receipt_exists:
                raise TruckyIdentityVerificationError("receipt_missing")
            if claim["status"] == "approved":
                existing = await conn.fetchval("""
                    SELECT EXISTS(SELECT 1 FROM unified_provider_links
                    WHERE provider='trucky' AND provider_account_id=$1
                      AND discord_user_id=$2 AND verified_at IS NOT NULL)
                """, account_id, discord_user_id)
                if not existing:
                    raise TruckyIdentityVerificationError("approved_link_inconsistent")
                # Refresh proof from this independently verified signed event;
                # old approved links without an evidence digest cannot enter
                # the new ledger until such a fresh event is observed.
                await conn.execute("""
                    UPDATE unified_provider_claims
                    SET membership_confirmed=TRUE,ownership_confirmed=TRUE,
                        ownership_evidence_kind='trucky_signed_job_steam_match',
                        ownership_evidence_sha256=$3,reviewed_at=NOW()
                    WHERE discord_user_id=$1 AND provider='trucky'
                      AND provider_account_id=$2 AND status='approved'
                """, discord_user_id, account_id, payload_sha256)
                await conn.execute("""
                    UPDATE unified_trucky_webhook_receipts SET review_status='verified'
                    WHERE payload_sha256=$1 AND candidate_job_id=$2
                      AND event_type='job_completed'
                """, payload_sha256, provider_job_id)
                return discord_user_id
            valid_window = await conn.fetchval("""
                SELECT $1::TIMESTAMPTZ >= $2::TIMESTAMPTZ
                   AND $1::TIMESTAMPTZ <= NOW() + INTERVAL '2 minutes'

            """, completed_at, claim["submitted_at"])
            if not valid_window:
                raise TruckyIdentityVerificationError("job_outside_claim_window")
            linked = await conn.fetchrow("""
                SELECT provider_account_id FROM unified_provider_links
                WHERE discord_user_id=$1 AND provider='trucky' FOR UPDATE
            """, discord_user_id)
            if linked and linked["provider_account_id"] != account_id:
                raise TruckyIdentityVerificationError("different_account_already_linked")
            result = await conn.execute("""
                INSERT INTO unified_provider_links
                    (provider,provider_account_id,discord_user_id,
                     provider_display_name,verified_at)
                VALUES('trucky',$1,$2,$3,NOW())
                ON CONFLICT(provider,provider_account_id) DO NOTHING
            """, account_id,discord_user_id,claim["provider_display_name"])
            if result != "INSERT 0 1":
                raise TruckyIdentityVerificationError("account_owned_by_another_driver")
            await conn.execute("""
                UPDATE unified_provider_claims
                SET status='approved',membership_confirmed=TRUE,
                    ownership_confirmed=TRUE,reviewed_at=NOW(),
                    ownership_evidence_kind='trucky_signed_job_steam_match',
                    ownership_evidence_sha256=$3
                WHERE discord_user_id=$1 AND provider='trucky'
                  AND provider_account_id=$2
            """,discord_user_id, account_id, payload_sha256)
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
            await conn.execute("""
                UPDATE unified_trucky_webhook_receipts SET review_status='verified'
                WHERE payload_sha256=$1 AND candidate_job_id=$2
                  AND event_type='job_completed'
            """, payload_sha256, provider_job_id)
            return discord_user_id


async def record_normalized_provider_job(
    pool, provider, provider_job_id, discord_user_id, distance, unit,
    *, provider_account_id=None, mileage_type="real", delivered_at=None, job_metadata=None
):
    """Idempotently stage a provider job in canonical miles; do not award mileage.

    Only call with provider-authenticated job IDs and trusted unit metadata.
    Cross-provider matching and rank/mileage credit remain separate approval gates.
    """
    import json
    from unified_distance_units import normalize_distance
    if provider not in ("trucksbook", "trucky"):
        raise ValueError("Unsupported job provider")
    if not isinstance(provider_job_id, str) or not provider_job_id.strip():
        raise ValueError("Missing provider job ID")
    if mileage_type not in ("real", "race"):
        raise ValueError("Unsupported mileage type")
    if provider_account_id is None:
        raise ValueError("A stable provider account ID is required")
    if (
        not isinstance(provider_account_id, str)
        or not provider_account_id.isascii()
        or not provider_account_id.isdecimal()
        or int(provider_account_id) <= 0
    ):
        raise ValueError("Invalid provider account ID")
    if job_metadata is not None:
        from unified_job_matching_v2 import _normalized_metadata
        _normalized_metadata(job_metadata)
    original, canonical_unit, miles = normalize_distance(distance, unit)
    real = miles if mileage_type == "real" else 0
    race = miles if mileage_type == "race" else 0
    audit_payload = json.dumps({
        "original_distance": str(original),
        "original_unit": canonical_unit,
        "canonical_miles": str(miles),
        "mileage_type": mileage_type,
        "job_metadata": job_metadata,
    })
    async with pool.acquire() as conn:
        async with conn.transaction():
            verified = await conn.fetchval("""
                SELECT EXISTS(
                    SELECT 1 FROM unified_provider_links
                    WHERE discord_user_id=$1 AND provider=$2
                      AND verified_at IS NOT NULL
                      AND provider_account_id=$3
                )
            """, discord_user_id, provider, provider_account_id)
            if not verified:
                raise ValueError("Provider account not verified for this driver")
            result = await conn.execute("""
                INSERT INTO unified_provider_job_events
                    (provider,provider_job_id,discord_user_id,provider_account_id,
                     real_miles,race_miles,delivered_at,source_payload)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb)
                ON CONFLICT(provider,provider_job_id) DO NOTHING
            """, provider,provider_job_id,discord_user_id,provider_account_id,
                 real,race,delivered_at,audit_payload)
            if result == "INSERT 0 0":
                previous = await conn.fetchrow("""
                    SELECT discord_user_id,provider_account_id,real_miles,race_miles,
                           delivered_at,source_payload
                    FROM unified_provider_job_events
                    WHERE provider=$1 AND provider_job_id=$2
                """, provider,provider_job_id)
                if (previous is None or previous["discord_user_id"] != discord_user_id
                        or previous["provider_account_id"] != provider_account_id
                        or previous["real_miles"] != real
                        or previous["race_miles"] != race
                        or previous["delivered_at"] != delivered_at
                        or (json.loads(previous["source_payload"]) if isinstance(previous["source_payload"], str)
                            else previous["source_payload"]) != json.loads(audit_payload)):
                    raise ValueError("Conflicting duplicate provider job requires review")
                return False
            return True

