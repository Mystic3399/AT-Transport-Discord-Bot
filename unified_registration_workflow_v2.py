"""Registration state is independent of job ownership and mileage eligibility."""
import os
import re

STAGING_ENVIRONMENT = '59f25900-3c94-4db1-a802-06e064d87274'
PRODUCTION_ENVIRONMENT = '72d029c5-98b4-4440-8492-8db12620d9da'
PROJECT_ID = '00313eef-3512-45ec-a4fa-fdcb815235a2'
PRODUCTION_SERVICE = '73f8817c-36be-423a-b68b-b457232cfcfd'


def enabled(environ=None):
    env = os.environ if environ is None else environ
    if env.get('RAILWAY_ENVIRONMENT_ID') == STAGING_ENVIRONMENT:
        return (env.get('AT_V2_ISOLATED_STAGING') == 'YES'
                and env.get('AT_V2_STAGING_REGISTRATION_ENABLED') == 'YES')
    # Future registration-only release. Owner approval is recorded out of band
    # before setting the approved SHA on this exact production deployment.
    # No staging/disposable flag can substitute for these production controls.
    sha = env.get('RAILWAY_GIT_COMMIT_SHA', '')
    return (env.get('RAILWAY_ENVIRONMENT_ID') == PRODUCTION_ENVIRONMENT
            and env.get('RAILWAY_PROJECT_ID') == PROJECT_ID
            and env.get('RAILWAY_SERVICE_ID') == PRODUCTION_SERVICE
            and not env.get('AT_V2_ISOLATED_STAGING')
            and not env.get('AT_V2_STAGING_REGISTRATION_ENABLED')
            and env.get('AT_V2_REGISTRATION_ENABLED') == 'YES'
            and bool(re.fullmatch(r'[0-9a-f]{40}', sha))
            and env.get('AT_V2_REGISTRATION_APPROVED_COMMIT') == sha)


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
        raise ValueError('V2 registration is not enabled for this deployment')
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
        raise ValueError('V2 registration is not enabled for this deployment')
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


async def list_registration_statuses(pool, page=1, page_size=10):
    """Read a bounded, consistent admin page; return no external account IDs.

    Provider/Steam evidence is read internally only to derive the same statuses
    used by the applicant embed. Registration and mileage tables are not written.
    """
    if not enabled() or os.getenv('AT_V2_ISOLATED_STAGING') != 'YES':
        raise ValueError('Verification overview is available only in isolated staging')
    if type(page) is not int or not 1 <= page <= 1000000:
        raise ValueError('Use a positive page number')
    if type(page_size) is not int or not 1 <= page_size <= 10:
        raise ValueError('Invalid page size')
    async with pool.acquire() as conn:
        async with conn.transaction(isolation='repeatable_read', readonly=True):
            total = await conn.fetchval('SELECT COUNT(*) FROM unified_driver_profiles')
            pages = max(1, (total + page_size - 1) // page_size)
            if page > pages:
                raise ValueError('That verification page does not exist')
            users = await conn.fetch("""
                SELECT p.discord_user_id,p.status AS profile_status,
                       s.verified_at IS NOT NULL AS steam_verified,
                       r.status AS registration_status,
                       EXISTS(SELECT 1 FROM unified_steam_auth_states a
                         WHERE a.discord_user_id=p.discord_user_id
                           AND a.expires_at>NOW() AND a.consumed_at IS NULL) AS steam_pending
                FROM unified_driver_profiles p
                LEFT JOIN unified_steam_links s USING(discord_user_id)
                LEFT JOIN unified_registration_sessions r USING(discord_user_id)
                ORDER BY p.discord_user_id LIMIT $1 OFFSET $2
            """, page_size, (page - 1) * page_size)
            ids = [r['discord_user_id'] for r in users]
            links = await conn.fetch("""
                SELECT discord_user_id,provider,provider_account_id,verified_at
                FROM unified_provider_links WHERE discord_user_id=ANY($1::bigint[])
            """, ids)
            claims = await conn.fetch("""
                SELECT discord_user_id,provider,provider_account_id,status,
                       ownership_confirmed,ownership_evidence_kind
                FROM unified_provider_claims WHERE discord_user_id=ANY($1::bigint[])
            """, ids)
    result = []
    for row in users:
        statuses = {}
        for provider in ('trucksbook', 'trucky'):
            link = next((r for r in links if r['discord_user_id'] == row['discord_user_id'] and r['provider'] == provider), None)
            claim = next((r for r in claims if r['discord_user_id'] == row['discord_user_id'] and r['provider'] == provider), None)
            statuses[provider] = account_status(link, claim, provider)
        complete = (row['registration_status'] == 'approved' and row['steam_verified']
                    and any(v in ('Linked', 'Verified') for v in statuses.values())
                    and row['profile_status'] != 'suspended'
                    and row['registration_status'] != 'cancelled')
        result.append({
            'discord_user_id': row['discord_user_id'],
            'steam': 'Verified' if row['steam_verified'] else 'Pending' if row['steam_pending'] else 'Not Linked',
            **statuses, 'registration': 'Complete' if complete else 'Incomplete',
        })
    return {'page': page, 'pages': pages, 'total': total, 'members': result}

