"""Company-approved TrucksBook mileage routing, distinct from provider ownership proof.

The owner accepts completed A&T onboarding as an account assignment for company
mileage. Never write provider verification or evidence flags from this policy.
"""
import os
import re

def enabled():
    return (os.getenv("AT_V2_COMPLETED_ONBOARDING_TB_CREDIT") == "YES"
            and os.getenv("RAILWAY_ENVIRONMENT_ID") == "72d029c5-98b4-4440-8492-8db12620d9da"
            and os.getenv("RAILWAY_SERVICE_ID") == "73f8817c-36be-423a-b68b-b457232cfcfd"
            and os.getenv("AT_V2_ISOLATED_STAGING") != "YES")

async def completed_owner(conn, account=None, user=None):
    """Resolve only an explicitly confirmed, current, conflict-free account."""
    if not enabled() or (account is None and user is None):
        return None
    if account is not None and (not isinstance(account, str) or
                                not re.fullmatch(r"[1-9][0-9]{0,18}", account)):
        return None
    if user is not None and (type(user) is not int or user <= 0):
        return None
    rows = await conn.fetch("""
        SELECT l.discord_user_id, l.provider_account_id
        FROM unified_provider_links l
        JOIN unified_provider_claims c ON c.discord_user_id=l.discord_user_id
          AND c.provider=l.provider AND c.provider_account_id=l.provider_account_id
        JOIN unified_driver_profiles p ON p.discord_user_id=l.discord_user_id
        JOIN unified_registration_sessions r ON r.discord_user_id=l.discord_user_id
        JOIN unified_steam_links s ON s.discord_user_id=l.discord_user_id
        JOIN driver_verifications v ON v.discord_user_id=l.discord_user_id
          AND v.verification_channel_id=r.verification_channel_id
        WHERE l.provider='trucksbook' AND p.status='active'
          AND l.provider_account_id ~ '^[1-9][0-9]{0,18}$'
          AND c.status IN ('pending','approved')
          AND r.status='approved' AND v.status='completed'
          AND v.completed_at IS NOT NULL AND s.verified_at IS NOT NULL
          AND c.submitted_at<=v.completed_at AND s.verified_at<=v.completed_at
          AND ($1::text IS NULL OR l.provider_account_id=$1)
          AND ($2::bigint IS NULL OR l.discord_user_id=$2)
          AND (v.trucksbook_user_id IS NULL OR v.trucksbook_user_id::text=l.provider_account_id)
          AND NOT EXISTS (
             SELECT 1 FROM driver_links d WHERE
               (d.discord_user_id=l.discord_user_id AND d.trucksbook_user_id IS NOT NULL
                  AND d.trucksbook_user_id::text<>l.provider_account_id)
               OR (d.discord_user_id<>l.discord_user_id
                  AND d.trucksbook_user_id::text=l.provider_account_id)
          )
          AND NOT EXISTS (
             SELECT 1 FROM driver_verifications other
             WHERE other.discord_user_id<>l.discord_user_id
               AND other.status='completed'
               AND other.trucksbook_user_id::text=l.provider_account_id
          )
        FOR SHARE OF l,c,p,r,s,v
    """, account, user)
    return dict(rows[0]) if len(rows) == 1 else None


async def restore_confirmation_receipts(pool, channel, bot_id):
    """Restore only genuine confirmation events written by this bot.

    The registration log is not provider ownership evidence. It records the
    explicit !confirm step after the account and Steam links were saved.
    """
    if not enabled() or channel is None or channel.permissions_for(channel.guild.default_role).view_channel:
        return 0
    latest = {}
    async for message in channel.history(limit=1000):
        if message.author.id != bot_id or message.webhook_id is not None:
            continue
        for embed in message.embeds:
            if embed.title != "Driver registration — confirmed":
                continue
            match = re.fullmatch(
                r"\*\*Driver:\*\* <@([0-9]+)>\n\*\*Channel:\*\* <#([0-9]+)>\n\*\*Outcome:\*\* confirmed\n"
                r"Linking is not independently verified ownership\. No mileage or roles awarded\.",
                embed.description or "")
            if match:
                key = (int(match[1]), int(match[2]))
                latest.setdefault(key, message.created_at)
    restored = 0
    async with pool.acquire() as conn:
        for (user, registration_channel), confirmed_at in latest.items():
            result = await conn.execute("""
                UPDATE driver_verifications v SET completed_at=$3
                FROM unified_registration_sessions r,
                     unified_steam_links s,
                     unified_provider_claims c,
                     unified_driver_profiles p,
                     unified_provider_links l
                WHERE v.discord_user_id=$1 AND v.verification_channel_id=$2
                  AND v.status='completed'
                  AND (v.completed_at IS NULL OR v.completed_at<$3)
                  AND r.discord_user_id=$1 AND r.verification_channel_id=$2
                  AND r.status='approved' AND r.created_at<=$3
                  AND p.discord_user_id=$1 AND p.status='active'
                  AND s.discord_user_id=$1 AND s.verified_at<=$3
                  AND c.discord_user_id=$1 AND c.provider='trucksbook'
                  AND c.status IN ('pending','approved') AND c.submitted_at<=$3
                  AND l.discord_user_id=$1 AND l.provider='trucksbook'
                  AND l.provider_account_id=c.provider_account_id
                  AND (v.trucksbook_user_id IS NULL OR v.trucksbook_user_id::text=l.provider_account_id)
            """, user, registration_channel, confirmed_at)
            restored += int(result == "UPDATE 1")
    return restored


async def recovery_report(conn, account):
    """Read-only, exact-account report after the actual recovery attempt."""
    row = await conn.fetchrow("""
        SELECT l.provider_account_id AS account, p.status AS profile,
               r.status AS registration, v.status AS confirmation,
               (v.verification_channel_id=r.verification_channel_id) AS same_channel,
               (c.submitted_at<=v.completed_at) AS claim_precedes_confirmation,
               (s.verified_at<=v.completed_at) AS steam_precedes_confirmation,
               COALESCE(d.real_miles,0) AS real_miles,
               (SELECT COUNT(*) FROM processed_jobs j
                  WHERE j.discord_user_id=l.discord_user_id
                    AND LOWER(j.statistics) LIKE 'real%') AS real_jobs,
               (SELECT COUNT(*) FROM automatic_mileage_holding_v2 h
                  WHERE h.provider='trucksbook' AND h.provider_account_id=$1
                    AND h.state<>'credited') AS held_jobs
        FROM unified_provider_links l
        LEFT JOIN unified_driver_profiles p USING(discord_user_id)
        LEFT JOIN unified_registration_sessions r USING(discord_user_id)
        LEFT JOIN unified_steam_links s USING(discord_user_id)
        LEFT JOIN driver_verifications v USING(discord_user_id)
        LEFT JOIN unified_provider_claims c ON c.discord_user_id=l.discord_user_id
            AND c.provider=l.provider AND c.provider_account_id=l.provider_account_id
        LEFT JOIN driver_progress d USING(discord_user_id)
        WHERE l.provider='trucksbook' AND l.provider_account_id=$1
    """, account)
    if row is None:
        return {"account":account,"linked":False}
    result = dict(row)
    result["eligible_assignment"] = (await completed_owner(conn,account=account)) is not None
    result["holds"] = [dict(x) for x in await conn.fetch("""
        SELECT reason_code,COUNT(*) AS jobs FROM automatic_mileage_holding_v2
        WHERE provider='trucksbook' AND provider_account_id=$1 AND state<>'credited'
        GROUP BY reason_code
    """,account)]
    return result
