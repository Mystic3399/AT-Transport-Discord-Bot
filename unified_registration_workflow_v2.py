"""Registration state is independent of job ownership and mileage eligibility."""
import os
import re

STAGING_ENVIRONMENT = '59f25900-3c94-4db1-a802-06e064d87274'


def enabled(environ=None):
    env = os.environ if environ is None else environ
    return (env.get('AT_V2_ISOLATED_STAGING') == 'YES'
            and env.get('AT_V2_STAGING_REGISTRATION_ENABLED') == 'YES'
            and env.get('RAILWAY_ENVIRONMENT_ID') == STAGING_ENVIRONMENT)


def account_status(link, claim, provider=None):
    if link:
        verified = (link['verified_at'] is not None and claim
                    and claim['status'] == 'approved' and claim['ownership_confirmed']
                    and claim['provider_account_id'] == link['provider_account_id']
                    and claim['ownership_evidence_kind'] in {
                        'trucky': {'trucky_signed_job_steam_match'},
                        'trucksbook': {'trucksbook_staff_reviewed_profile_steam_mapping'},
                        None: {'trucky_signed_job_steam_match','trucksbook_staff_reviewed_profile_steam_mapping'},
                    }[provider])
        return 'Verified' if verified else 'Linked'
    return 'Pending' if claim and claim['status'] != 'rejected' else 'Not Linked'


async def state(pool, user_id):
    async with pool.acquire() as conn:
        session = await conn.fetchrow('SELECT * FROM unified_registration_sessions WHERE discord_user_id=$1', user_id)
        steam = await conn.fetchrow('SELECT steam_id64,verified_at FROM unified_steam_links WHERE discord_user_id=$1', user_id)
        links = await conn.fetch('SELECT * FROM unified_provider_links WHERE discord_user_id=$1', user_id)
        claims = await conn.fetch('SELECT * FROM unified_provider_claims WHERE discord_user_id=$1', user_id)
        steam_pending = await conn.fetchval('SELECT EXISTS(SELECT 1 FROM unified_steam_auth_states WHERE discord_user_id=$1 AND expires_at>NOW() AND consumed_at IS NULL)', user_id)
    accounts = {}
    for provider in ('trucksbook', 'trucky'):
        link = next((r for r in links if r['provider'] == provider), None)
        claim = next((r for r in claims if r['provider'] == provider), None)
        source = link or claim
        accounts[provider] = dict(status=account_status(link, claim, provider),
            account_id=source['provider_account_id'] if source else None,
            display_name=source['provider_display_name'] if source else None)
    steam_verified = bool(steam and steam['verified_at'] is not None)
    ready = steam_verified and any(a['status'] in ('Linked', 'Verified') for a in accounts.values())
    return dict(session=dict(session) if session else None,
                steam_id64=steam['steam_id64'] if steam_verified else None,
                steam_status='Verified' if steam_verified else 'Pending' if steam_pending else 'Not Linked',
                accounts=accounts, ready=ready)


async def link_supported_account(pool, user_id, provider, account_id, canonical_name):
    """Provider existence/membership lookup allows Linked, not ownership Verified.

    Caller must use the existing provider lookup, never a supplied display name.
    No historical tables, provider ownership flags or mileage are written.
    """
    if not enabled():
        raise ValueError('V2 registration is not enabled in isolated staging')
    if provider not in ('trucksbook', 'trucky') or not isinstance(account_id, str) or not re.fullmatch(r'[0-9]{1,30}', account_id) or int(account_id) <= 0:
        raise ValueError('Use a numeric provider account ID')
    if not isinstance(canonical_name, str) or not canonical_name.strip():
        raise ValueError('Provider account lookup did not return an account name')
    async with pool.acquire() as conn:
        async with conn.transaction():
            session = await conn.fetchrow('SELECT status FROM unified_registration_sessions WHERE discord_user_id=$1 FOR UPDATE', user_id)
            if not session or session['status'] == 'cancelled':
                raise ValueError('Start /register first')
            if not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM unified_steam_links WHERE discord_user_id=$1 AND verified_at IS NOT NULL)', user_id):
                raise ValueError('Verify Steam first using !linksteam')
            link = await conn.fetchrow('SELECT provider_account_id FROM unified_provider_links WHERE discord_user_id=$1 AND provider=$2 FOR UPDATE', user_id, provider)
            claim = await conn.fetchrow('SELECT provider_account_id FROM unified_provider_claims WHERE discord_user_id=$1 AND provider=$2 FOR UPDATE', user_id, provider)
            if any(r and r['provider_account_id'] != account_id for r in (link, claim)):
                raise ValueError('An existing account is recorded; contact management to change it')
            conflict = await conn.fetchval('''SELECT EXISTS(
                SELECT 1 FROM unified_provider_links WHERE provider=$1 AND provider_account_id=$2 AND discord_user_id<>$3
                UNION ALL SELECT 1 FROM unified_provider_claims WHERE provider=$1 AND provider_account_id=$2 AND discord_user_id<>$3
            )''', provider, account_id, user_id)
            if conflict:
                raise ValueError('This account is already recorded for another driver')
            await conn.execute('''INSERT INTO unified_provider_claims
                (discord_user_id,provider,provider_account_id,provider_display_name)
                VALUES($1,$2,$3,$4) ON CONFLICT(discord_user_id,provider) DO NOTHING''', user_id, provider, account_id, canonical_name)
            await conn.execute('''INSERT INTO unified_provider_links
                (provider,provider_account_id,discord_user_id,provider_display_name)
                VALUES($1,$2,$3,$4) ON CONFLICT(discord_user_id,provider) DO NOTHING''', provider, account_id, user_id, canonical_name)
    return await complete_if_ready(pool, user_id)


async def complete_if_ready(pool, user_id):
    if not enabled():
        raise ValueError('V2 registration is not enabled in isolated staging')
    # Lock session and Steam/provider rows so cancellation and account changes
    # cannot race the onboarding decision. No mileage eligibility is implied.
    async with pool.acquire() as conn:
        async with conn.transaction():
            session = await conn.fetchrow('SELECT status FROM unified_registration_sessions WHERE discord_user_id=$1 FOR UPDATE', user_id)
            if not session or session['status'] == 'cancelled':
                return False
            profile = await conn.fetchrow('SELECT status FROM unified_driver_profiles WHERE discord_user_id=$1 FOR UPDATE', user_id)
            if not profile or profile['status'] == 'suspended':
                return False
            steam = await conn.fetchrow('SELECT verified_at FROM unified_steam_links WHERE discord_user_id=$1 FOR UPDATE', user_id)
            links = await conn.fetch('SELECT provider FROM unified_provider_links WHERE discord_user_id=$1 FOR UPDATE', user_id)
            if not steam or steam['verified_at'] is None or not links:
                return False
            await conn.execute("UPDATE unified_registration_sessions SET status='approved',updated_at=NOW() WHERE discord_user_id=$1 AND status<>'approved'", user_id)
            await conn.execute("UPDATE unified_driver_profiles SET status='active',activated_at=COALESCE(activated_at,NOW()) WHERE discord_user_id=$1 AND status='pending'", user_id)
    return True
