"""Management-only reviewed Trucky held-credit releases.

Never accept a typed mileage amount as proof. Preview pins the exact retained
authenticated company job evidence, driver and reviewer. CONFIRM revalidates
every field, the registered Steam-backed account, active company membership,
database identity and the production activation gate. Cross-provider duplicate
reconciliation uses the same safe engine as automatic allocation.
"""
import os
import re

from account_unlink import has_valid_tracking_link
from trucky_linked_allocation_v2 import (
    _settle_candidate, activation_allowed, vault_candidate,
)
from unified_production_mileage_gate_v2 import read_only_database_fingerprint


SCHEMA = """
CREATE TABLE IF NOT EXISTS held_mileage_approval_previews_v2 (
  preview_id BIGSERIAL PRIMARY KEY,
  guild_id BIGINT NOT NULL,
  channel_id BIGINT NOT NULL,
  reviewer_id BIGINT NOT NULL,
  discord_user_id BIGINT NOT NULL,
  provider_job_id TEXT NOT NULL,
  provider_account_id TEXT NOT NULL,
  evidence_sha256 TEXT NOT NULL,
  mileage_type TEXT NOT NULL,
  miles NUMERIC(24,10) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW()+INTERVAL '15 minutes',
  applied_at TIMESTAMPTZ,
  outcome TEXT
);
CREATE INDEX IF NOT EXISTS held_mileage_approval_lookup_v2
 ON held_mileage_approval_previews_v2
 (guild_id,channel_id,reviewer_id,discord_user_id,provider_job_id,preview_id DESC);
CREATE TABLE IF NOT EXISTS held_mileage_approval_audit_v2 (
  audit_id BIGSERIAL PRIMARY KEY,
  preview_id BIGINT NOT NULL REFERENCES held_mileage_approval_previews_v2(preview_id),
  reviewer_id BIGINT NOT NULL,
  discord_user_id BIGINT NOT NULL,
  provider_job_id TEXT NOT NULL,
  evidence_sha256 TEXT NOT NULL,
  mileage_type TEXT NOT NULL,
  credited_miles NUMERIC(24,10) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(provider_job_id)
);
"""


class HeldMileageApprovalError(ValueError):
    pass


def _valid_ids(*ids):
    return all(type(value) is int and 0 < value <= 9223372036854775807 for value in ids)


def _valid_job(job_id):
    return (isinstance(job_id, str) and
            re.fullmatch(r"[1-9][0-9]{0,19}", job_id) is not None and
            int(job_id) <= 9223372036854775807)


async def _verified_driver_for_candidate(conn, candidate):
    user = candidate["discord_user_id"]
    account = candidate["provider_account_id"]
    if not await has_valid_tracking_link(conn, user):
        return False
    return bool(await conn.fetchval("""
        SELECT EXISTS(
            SELECT 1 FROM unified_provider_links l
            JOIN unified_provider_claims c
              ON c.provider=l.provider AND c.provider_account_id=l.provider_account_id
             AND c.discord_user_id=l.discord_user_id
            JOIN unified_steam_links s USING(discord_user_id)
            JOIN unified_driver_profiles p USING(discord_user_id)
            JOIN unified_registration_sessions r USING(discord_user_id)
            WHERE l.provider='trucky' AND l.provider_account_id=$1
              AND l.discord_user_id=$2 AND l.verified_at IS NOT NULL
              AND s.verified_at IS NOT NULL AND p.status='active' AND r.status='approved'
              AND c.status='approved' AND c.ownership_confirmed=TRUE
              AND c.membership_confirmed=TRUE
              AND c.ownership_evidence_kind='trucky_signed_job_steam_match'
              AND c.ownership_evidence_sha256 IS NOT NULL
              AND (c.expires_at IS NULL OR c.expires_at > NOW())
        )
    """, account, user))


async def _get_candidate(conn, user, job_id):
    if not _valid_ids(user) or not _valid_job(job_id):
        raise HeldMileageApprovalError("Use a valid Discord driver and Trucky job ID.")
    rows = await conn.fetch("""
        SELECT provider,provider_job_id,provider_account_id,source_kind,evidence
        FROM pending_provider_job_history_v2
        WHERE provider='trucky' AND provider_job_id=$1 AND source_kind='authenticated_api'
        ORDER BY payload_sha256
    """, job_id)
    if not rows:
        raise HeldMileageApprovalError("That job is not in authenticated Trucky history.")
    candidate = vault_candidate([dict(row) for row in rows], user)
    if candidate is None:
        raise HeldMileageApprovalError("Job evidence is incomplete or conflicting.")
    if not await _verified_driver_for_candidate(conn, candidate):
        raise HeldMileageApprovalError("Driver is unlinked or Steam/Trucky ownership is not verified.")
    return candidate


async def preview(pool, *, guild_id, channel_id, reviewer_id, discord_user_id, job_id):
    if not _valid_ids(guild_id,channel_id,reviewer_id,discord_user_id):
        raise HeldMileageApprovalError("Invalid review identity.")
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(SCHEMA)
            candidate = await _get_candidate(conn,discord_user_id,job_id)
            already = await conn.fetchval("""
                SELECT EXISTS(SELECT 1 FROM unified_v2_production_credit_ledger
                              WHERE provider='trucky' AND provider_job_id=$1)
            """,job_id)
            if already:
                raise HeldMileageApprovalError("This Trucky job already has a credit; it cannot be awarded twice.")
            held_reason = await conn.fetchval("""
                SELECT reason_code FROM unified_v2_production_mileage_decisions
                WHERE provider='trucky' AND provider_job_id=$1
                  AND discord_user_id=$2 AND decision='pending_reconciliation'
                ORDER BY decision_id DESC LIMIT 1
            """,job_id,discord_user_id)
            preview_id=await conn.fetchval("""
                INSERT INTO held_mileage_approval_previews_v2
                  (guild_id,channel_id,reviewer_id,discord_user_id,provider_job_id,
                   provider_account_id,evidence_sha256,mileage_type,miles)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING preview_id
            """,guild_id,channel_id,reviewer_id,discord_user_id,job_id,
                 candidate["provider_account_id"],candidate["source_sha256"],
                 candidate["mileage_type"],candidate["miles"])
    return {"preview_id": preview_id, "job_id":job_id,
            "miles":str(candidate["miles"]), "mileage_type":candidate["mileage_type"],
            "held_reason":held_reason or "awaiting_credit_review",
            "activation_ready":activation_allowed(),
            "mileage_changed":False}


async def confirm(pool, *, guild_id, channel_id, reviewer_id, discord_user_id, job_id):
    if not _valid_ids(guild_id,channel_id,reviewer_id,discord_user_id) or not _valid_job(job_id):
        raise HeldMileageApprovalError("Invalid review identity.")
    if not activation_allowed():
        raise HeldMileageApprovalError(
            "The production linked-mileage activation gate is not approved for this deployment.")
    from trucky_membership_adapter import fetch_current_member_ids
    # Always fetch the authoritative roster before attempting to credit.
    members=await fetch_current_member_ids()
    async with pool.acquire() as conn:
        async with conn.transaction():
            actual=await read_only_database_fingerprint(conn)
            if actual != os.getenv("AT_V2_LINKED_MILEAGE_DATABASE_SHA256"):
                raise HeldMileageApprovalError("Production database identity check failed.")
            row=await conn.fetchrow("""
                SELECT * FROM held_mileage_approval_previews_v2
                WHERE guild_id=$1 AND channel_id=$2 AND reviewer_id=$3
                  AND discord_user_id=$4 AND provider_job_id=$5
                ORDER BY preview_id DESC LIMIT 1 FOR UPDATE
            """,guild_id,channel_id,reviewer_id,discord_user_id,job_id)
            if row is None or row["applied_at"] is not None or row["expires_at"] <= row["created_at"]:
                raise HeldMileageApprovalError("Run a new !heldmiles preview before confirming.")
            # Database time, not the bot process clock, decides expiry.
            live=await conn.fetchval(
                "SELECT expires_at>NOW() FROM held_mileage_approval_previews_v2 WHERE preview_id=$1",
                row["preview_id"])
            if live is not True:
                raise HeldMileageApprovalError("The 15-minute preview expired.")
            candidate=await _get_candidate(conn,discord_user_id,job_id)
            if (candidate["provider_account_id"]!=row["provider_account_id"]
                or candidate["source_sha256"]!=row["evidence_sha256"]
                or candidate["mileage_type"]!=row["mileage_type"]
                or candidate["miles"]!=row["miles"]):
                raise HeldMileageApprovalError("Job evidence changed. Review a new preview.")
            # This may still return a hold reason. Manual review invokes normal
            # journey matching, but never bypasses ambiguous or duplicate jobs.
            outcome=await _settle_candidate(conn,candidate,members,reviewed=True)
            if outcome not in ("credited_real","credited_race"):
                return {"status":outcome,"credited":False,"job_id":job_id}
            await conn.execute("""
                INSERT INTO held_mileage_approval_audit_v2
                   (preview_id,reviewer_id,discord_user_id,provider_job_id,
                    evidence_sha256,mileage_type,credited_miles)
                VALUES($1,$2,$3,$4,$5,$6,$7)
            """,row["preview_id"],reviewer_id,discord_user_id,job_id,
                 candidate["source_sha256"],candidate["mileage_type"],candidate["miles"])
            await conn.execute("""
                UPDATE held_mileage_approval_previews_v2
                SET applied_at=NOW(),outcome=$2
                WHERE preview_id=$1 AND applied_at IS NULL
            """,row["preview_id"],outcome)
    return {"status":outcome,"credited":True,"job_id":job_id,
            "miles":str(candidate["miles"]), "mileage_type":candidate["mileage_type"],
            "discord_user_id":discord_user_id}
