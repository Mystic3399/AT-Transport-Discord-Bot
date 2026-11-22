"""Repair missing provider-link mirrors from independently approved registrations.

Never promotes a self-entered ID, changes a driver balance, or overrides a
conflicting owner. Safe to rerun before each automatic mileage allocation.
"""
import json

PROOF = {
    "trucksbook": "trucksbook_staff_reviewed_profile_steam_mapping",
    "trucky": "trucky_signed_job_steam_match",
}

async def repair_approved_provider_links(pool):
    result = {"eligible": 0, "repaired": 0, "conflicts": 0, "unchanged": 0}
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Only approved, reviewed, independently evidenced claims for
            # active drivers with a verified Steam account may be mirrored.
            rows = await conn.fetch("""
                SELECT c.discord_user_id, c.provider, c.provider_account_id,
                       c.provider_display_name, c.reviewed_at, c.reviewed_by,
                       c.ownership_evidence_kind
                FROM unified_provider_claims c
                JOIN unified_driver_profiles p USING(discord_user_id)
                JOIN unified_steam_links s USING(discord_user_id)
                WHERE p.status='active' AND s.verified_at IS NOT NULL
                  AND c.status='approved'
                  AND c.ownership_confirmed=TRUE
                  AND c.membership_confirmed=TRUE
                  AND c.reviewed_at IS NOT NULL
                  AND c.reviewed_by IS NOT NULL
                  AND c.provider_account_id ~ '^[1-9][0-9]*$'
                ORDER BY c.provider,c.discord_user_id
            """)
            for c in rows:
                provider=c["provider"]
                account=c["provider_account_id"]
                user=c["discord_user_id"]
                if c["ownership_evidence_kind"] != PROOF.get(provider):
                    continue
                result["eligible"] += 1
                # Prevent a cross-table mismatch from being silently overwritten.
                conflict = await conn.fetchval("""
                    SELECT EXISTS(
                        SELECT 1 FROM unified_provider_links l
                        WHERE l.provider=$1
                          AND (l.provider_account_id=$2 AND l.discord_user_id<>$3
                            OR l.discord_user_id=$3 AND l.provider_account_id<>$2)
                    )
                """, provider,account,user)
                if provider=="trucksbook":
                    conflict = conflict or await conn.fetchval("""
                        SELECT EXISTS(
                          SELECT 1 FROM driver_links d
                          WHERE (d.discord_user_id=$1 AND d.trucksbook_user_id IS NOT NULL
                                 AND d.trucksbook_user_id::text<>$2)
                             OR (d.discord_user_id<>$1
                                 AND d.trucksbook_user_id::text=$2))
                    """,user,account)
                if conflict:
                    result["conflicts"] += 1
                    continue
                updated = await conn.fetchval("""
                    INSERT INTO unified_provider_links
                        (provider,provider_account_id,discord_user_id,
                         provider_display_name,verified_at,verified_by)
                    VALUES ($1,$2,$3,$4,$5,$6)
                    ON CONFLICT(provider,provider_account_id)
                    DO UPDATE SET
                      verified_at=COALESCE(unified_provider_links.verified_at,EXCLUDED.verified_at),
                      verified_by=COALESCE(unified_provider_links.verified_by,EXCLUDED.verified_by)
                    WHERE unified_provider_links.discord_user_id=EXCLUDED.discord_user_id
                      AND unified_provider_links.verified_at IS NULL
                    RETURNING discord_user_id
                """,provider,account,user,c["provider_display_name"],
                    c["reviewed_at"],c["reviewed_by"])
                if updated is None:
                    result["unchanged"] += 1
                else:
                    result["repaired"] += 1
    async with pool.acquire() as conn:
        result["trucksbook_source_account_diagnostics"] = [dict(row) for row in await conn.fetch("""
            WITH accounts AS (
              SELECT DISTINCT provider_account_id AS account
              FROM pending_provider_job_history_v2
              WHERE provider='trucksbook' AND source_kind='verified_webhook_source'
                AND provider_account_id IS NOT NULL
            ), matches AS (
              SELECT a.account,
                EXISTS(SELECT 1 FROM driver_links d WHERE d.trucksbook_user_id::text=a.account) AS legacy,
                EXISTS(SELECT 1 FROM driver_verifications v WHERE v.trucksbook_user_id::text=a.account AND v.status='completed') AS completed,
                EXISTS(SELECT 1 FROM unified_provider_links l WHERE l.provider='trucksbook' AND l.provider_account_id=a.account AND l.verified_at IS NOT NULL) AS verified_link,
                EXISTS(SELECT 1 FROM unified_provider_claims c WHERE c.provider='trucksbook' AND c.provider_account_id=a.account AND c.status='approved' AND c.ownership_confirmed) AS approved_claim,
                EXISTS(SELECT 1 FROM unified_provider_claims c WHERE c.provider='trucksbook' AND c.provider_account_id=a.account AND c.status='pending') AS pending_claim
              FROM accounts a
            )
            SELECT legacy,completed,verified_link,approved_claim,pending_claim,
                   COUNT(*)::INT AS accounts FROM matches
            GROUP BY legacy,completed,verified_link,approved_claim,pending_claim
            ORDER BY accounts DESC
        """)]
    return result
