"""Management account detachment with an immutable snapshot; never deletes career data."""
import hashlib
import json
import re
import unicodedata

SCHEMA='''
CREATE TABLE IF NOT EXISTS driver_account_unlink_audit (
    audit_id BIGSERIAL PRIMARY KEY, discord_user_id BIGINT NOT NULL,
    actor_discord_id BIGINT NOT NULL, scope TEXT NOT NULL, providers TEXT[] NOT NULL,
    before_state JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
'''
SCOPES={'trucksbook','trucky','steam','all'}


def allowed(member,role_ids=None):
    guild=getattr(member,'guild',None)
    if not guild or getattr(member,'bot',False): return False
    if member.id==guild.owner_id: return True
    names={'management','owner','co owner','owner chief executive officer','co owner chief operator officer'}
    return any(' '.join(re.sub('[^a-z0-9]+',' ',unicodedata.normalize('NFKC',role.name).casefold()).split()) in names
               for role in member.roles if role_ids is None or getattr(role,'id',None) in role_ids)


async def resolve_target(conn,guild,value):
    match=re.fullmatch(r'(?:<[@#]!?([0-9]{15,20})>|([0-9]{15,20}))',value.strip())
    if not match: raise ValueError('Choose a driver suggestion, @mention, Discord user ID, or private onboarding channel ID.')
    number=int(match[1] or match[2])
    if number>9223372036854775807: raise ValueError('Invalid Discord ID.')
    if value.strip().startswith('<@') or guild.get_member(number) is not None:
        return number
    rows=await conn.fetch('''SELECT discord_user_id FROM unified_registration_sessions WHERE verification_channel_id=$1
        UNION SELECT discord_user_id FROM driver_verifications WHERE verification_channel_id=$1''',number)
    ids={r['discord_user_id'] for r in rows}
    if len(ids)>1: raise ValueError('That channel has conflicting registration records. Use the driver mention instead.')
    return ids.pop() if ids else number


async def snapshot(conn,user_id):
    legacy=await conn.fetchrow('SELECT discord_user_id,trucksbook_user_id,trucksbook_name FROM driver_links WHERE discord_user_id=$1',user_id)
    steam=await conn.fetchrow('SELECT steam_id64,verified_at FROM unified_steam_links WHERE discord_user_id=$1',user_id)
    links=await conn.fetch('SELECT * FROM unified_provider_links WHERE discord_user_id=$1 ORDER BY provider',user_id)
    claims=await conn.fetch('SELECT * FROM unified_provider_claims WHERE discord_user_id=$1 ORDER BY provider',user_id)
    trucky=await conn.fetchrow('SELECT * FROM trucky_driver_links WHERE discord_user_id=$1',user_id)
    request=await conn.fetchrow('SELECT * FROM trucky_link_requests WHERE discord_user_id=$1',user_id)
    data=dict(legacy=dict(legacy) if legacy else None,steam=dict(steam) if steam else None,
              links=[dict(r) for r in links],claims=[dict(r) for r in claims],
              trucky=dict(trucky) if trucky else None,request=dict(request) if request else None)
    if not (steam or links or claims or trucky or request or (legacy and legacy['trucksbook_name']!=f'__unlinked_driver_{user_id}__')):
        raise ValueError('No linked accounts were found for this driver.')
    encoded=json.dumps(data,sort_keys=True,default=str,separators=(',',':'))
    return data,hashlib.sha256(encoded.encode()).hexdigest(),encoded


async def unlink(pool,user_id,actor_id,scope,expected):
    if scope not in SCOPES: raise ValueError('Choose a supported account type.')
    providers=['trucksbook','trucky'] if scope in {'steam','all'} else [scope]
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Use the same session/profile lock order as identity verification.
            await conn.fetchrow('SELECT discord_user_id FROM unified_registration_sessions WHERE discord_user_id=$1 FOR UPDATE',user_id)
            await conn.fetchrow('SELECT discord_user_id FROM unified_driver_profiles WHERE discord_user_id=$1 FOR UPDATE',user_id)
            await conn.fetchrow('SELECT discord_user_id FROM driver_links WHERE discord_user_id=$1 FOR UPDATE',user_id)
            data,fingerprint,encoded=await snapshot(conn,user_id)
            if fingerprint!=expected: raise ValueError('The linked accounts changed. Run /unlink again to review the current accounts.')
            await conn.execute('''INSERT INTO driver_account_unlink_audit
                (discord_user_id,actor_discord_id,scope,providers,before_state) VALUES($1,$2,$3,$4,$5::jsonb)''',
                user_id,actor_id,scope,providers,encoded)
            await conn.execute('DELETE FROM unified_provider_links WHERE discord_user_id=$1 AND provider=ANY($2::text[])',user_id,providers)
            await conn.execute('DELETE FROM unified_provider_claims WHERE discord_user_id=$1 AND provider=ANY($2::text[])',user_id,providers)
            if 'trucky' in providers:
                await conn.execute('DELETE FROM trucky_driver_links WHERE discord_user_id=$1',user_id)
                await conn.execute('DELETE FROM trucky_link_requests WHERE discord_user_id=$1',user_id)
            if 'trucksbook' in providers:
                # Keep the legacy row/FKs for history, but retire its routing identity.
                await conn.execute("UPDATE driver_links SET trucksbook_user_id=NULL,trucksbook_name='__unlinked_driver_'||discord_user_id::text||'__' WHERE discord_user_id=$1",user_id)
                await conn.execute('UPDATE driver_verifications SET trucksbook_user_id=NULL,trucksbook_name=NULL WHERE discord_user_id=$1',user_id)
            if scope in {'steam','all'}:
                await conn.execute('DELETE FROM unified_steam_links WHERE discord_user_id=$1',user_id)
                await conn.execute('DELETE FROM driver_card_steam_names WHERE discord_user_id=$1',user_id)
                await conn.execute('UPDATE unified_steam_auth_states SET consumed_at=NOW() WHERE discord_user_id=$1 AND consumed_at IS NULL',user_id)
            remaining=await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM unified_provider_links WHERE discord_user_id=$1)
                OR EXISTS(SELECT 1 FROM driver_links WHERE discord_user_id=$1 AND trucksbook_user_id IS NOT NULL)''',user_id)
            if not remaining:
                await conn.execute("UPDATE unified_driver_profiles SET status='pending' WHERE discord_user_id=$1 AND status<>'suspended'",user_id)
                await conn.execute("UPDATE unified_registration_sessions SET status='pending',updated_at=NOW() WHERE discord_user_id=$1",user_id)
            return data
