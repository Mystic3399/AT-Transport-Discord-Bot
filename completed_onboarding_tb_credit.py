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
