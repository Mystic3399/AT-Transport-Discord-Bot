"""Read-only company totals publisher. Never awards or rewrites driver mileage."""
import hashlib
import hmac
import json
import math
import os
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit
import aiohttp

async def collect_snapshot(pool, guild, read_profile, membership):
    if guild is None:
        raise RuntimeError('Company Discord server unavailable')
    if not guild.chunked:
        await guild.chunk(cache=True)
    real = race = jobs = drivers = 0
    profiles = []
    async with pool.acquire() as connection:
        async with connection.transaction(isolation='repeatable_read', readonly=True):
            has_credited_totals = bool(await connection.fetchval("""
                SELECT to_regclass('public.unified_v2_production_mileage_baselines') IS NOT NULL
                   AND to_regclass('public.unified_v2_production_mileage_totals') IS NOT NULL
            """))
            for member in guild.members:
                if member.bot or membership.get(member.id, {}).get('company') == 'Former company driver':
                    continue
                profile = await read_profile(connection, member.id)
                if profile is None:
                    continue
                if has_credited_totals:
                    credited = await connection.fetchrow("""
                        SELECT b.trucksbook_real_miles
                                   + COALESCE(t.combined_real_miles, 0) AS real_miles,
                               COALESCE(t.trucky_race_miles, 0) AS trucky_race_miles
                        FROM unified_v2_production_mileage_baselines b
                        LEFT JOIN unified_v2_production_mileage_totals t USING(discord_user_id)
                        WHERE b.discord_user_id=$1
                    """, member.id)
                    # Older accounts may not have a migrated baseline yet.
                    # Preserve their existing profile totals until migration.
                    if credited is not None:
                        # Canonical totals include both games and already apply
                        # cross-provider duplicate decisions, independently of
                        # Discord progression-role activation.
                        profile = dict(profile)
                        profile['real_miles'] = credited['real_miles']
                        profile['trucky_race_miles'] = credited['trucky_race_miles']
                drivers += 1
                real += int(profile['real_miles'])
                race += float(profile.get('trucky_race_miles') or 0)
                jobs += int(profile['jobs_completed'])
                # The established profile count covers Real deliveries; include
                # separately credited Trucky Race deliveries once, from its ledger.
                race_jobs = int(await connection.fetchval("""SELECT COUNT(*) FROM unified_v2_production_credit_ledger
                    WHERE discord_user_id=$1 AND provider='trucky' AND mileage_type='race'""", member.id) or 0)
                personal = dict(discordUserId=str(member.id),realMiles=int(profile['real_miles']),
                                raceMiles=float(profile.get('trucky_race_miles') or 0),
                                completedJobs=int(profile['jobs_completed'])+race_jobs)
                if not all(math.isfinite(personal[key]) and 0 <= personal[key] <= 9007199254740991
                           for key in ('realMiles','raceMiles','completedJobs')):
                    raise RuntimeError('Invalid driver profile totals; previous snapshot preserved')
                profiles.append(personal)
                jobs += race_jobs
    totals = dict(activeDrivers=drivers, discordMembers=sum(not m.bot for m in guild.members),
                  realMiles=real, raceMiles=race, completedJobs=jobs)
    if not all(math.isfinite(value) and 0 <= value <= 9007199254740991 for value in totals.values()):
        raise RuntimeError('Invalid company totals; previous snapshot preserved')
    return dict(version=1,source='at-command',eventId=uuid.uuid4().hex,
                generatedAt=datetime.now(timezone.utc).isoformat(),
                mileagePolicy='higher-provider-mileage',totals=totals,profiles=profiles)

async def game_coverage(pool, profiles):
    """Read aggregate credited deliveries by their recorded source game."""
    async with pool.acquire() as connection:
        async with connection.transaction(readonly=True):
            rows = await connection.fetch("""
                SELECT c.provider, c.mileage_type,
                       COALESCE(NULLIF(e.source_payload->'job_metadata'->>'game',''),
                                NULLIF(e.source_payload->'classification_evidence'->>'game',''),
                                'unknown') AS game,
                       COUNT(*) AS jobs, SUM(c.miles) AS miles
                FROM unified_v2_production_credit_ledger c
                LEFT JOIN unified_provider_job_events e
                  ON e.provider=c.provider AND e.provider_job_id=c.provider_job_id
                 AND e.discord_user_id=c.discord_user_id
                WHERE c.discord_user_id=ANY($1::bigint[])
                GROUP BY c.provider, c.mileage_type, game
                ORDER BY c.provider, c.mileage_type, game
            """, [int(profile['discordUserId']) for profile in profiles])
    return [dict(provider=row['provider'], game=row['game'],
                 mileageType=row['mileage_type'], jobs=int(row['jobs']),
                 miles=float(row['miles'])) for row in rows]

def signed_payload(snapshot, secret, timestamp=None):
    body = json.dumps(snapshot,separators=(',',':'))
    timestamp = str(int(time.time()) if timestamp is None else timestamp)
    path = '/api/integrations/command-bridge'
    signature = hmac.new(secret.encode(),f'POST\n{path}\n{timestamp}\n{body}'.encode(),hashlib.sha256).hexdigest()
    return body, {'content-type':'application/json','x-at-timestamp':timestamp,'x-at-signature':signature}

async def publish(pool, guild, read_profile, membership, career_builder=None):
    secret = os.getenv('AT_COMMAND_BRIDGE_SECRET','')
    if os.getenv('AT_COMMAND_BRIDGE_ENABLED') != 'true' or len(secret) < 32:
        return
    snapshot = await collect_snapshot(pool,guild,read_profile,membership)
    body, headers = signed_payload(snapshot,secret)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        async with session.post('https://at-transport-ltd.com/api/integrations/command-bridge',
                                data=body.encode(),headers=headers,allow_redirects=False) as response:
            if response.status not in (200,202):
                raise RuntimeError(f'Website bridge HTTP {response.status}')
    print('WEBSITE COMPANY TOTALS PUBLISHED ' + json.dumps(snapshot['totals']) + f" profiles={len(snapshot['profiles'])}",flush=True)

    if career_builder:
        try:
            await publish_careers(guild, career_builder)
        except Exception as error:
            print('WEBSITE CAREER SYNC RETRY: '+type(error).__name__,flush=True)
    try:
        print('WEBSITE CREDITED GAME COVERAGE ' + json.dumps(await game_coverage(pool, snapshot['profiles'])), flush=True)
    except Exception as error:
        print('WEBSITE GAME COVERAGE UNAVAILABLE: ' + type(error).__name__, flush=True)

_link_cache = {}
_link_loaded = 0

def cached_hub_link(user_id):
    if time.monotonic()-_link_loaded>180:
        return None
    return _link_cache.get(int(user_id))

def service_headers(method, path, body=''):
    secret=os.getenv('AT_COMMAND_BRIDGE_SECRET','')
    timestamp=str(int(time.time()))
    signature=hmac.new(secret.encode(),f'{method}\n{path}\n{timestamp}\n{body}'.encode(),hashlib.sha256).hexdigest()
    return {'content-type':'application/json','x-at-timestamp':timestamp,'x-at-signature':signature}

def valid_service_request(request):
    secret=os.getenv('AT_COMMAND_BRIDGE_SECRET','')
    if len(secret)<32 or os.getenv('AT_COMMAND_BRIDGE_ENABLED')!='true': return False
    stamp=request.headers.get('x-at-timestamp','')
    if not stamp.isdigit() or abs(time.time()-int(stamp))>300: return False
    expected=hmac.new(secret.encode(),f'{request.method}\n{request.path_qs}\n{stamp}\n'.encode(),hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected,request.headers.get('x-at-signature',''))

async def refresh_hub_links(force=False):
    global _link_loaded, _link_cache
    if not force and time.monotonic()-_link_loaded<60: return
    if os.getenv('AT_COMMAND_BRIDGE_ENABLED')!='true' or len(os.getenv('AT_COMMAND_BRIDGE_SECRET',''))<32: return
    path='/api/integrations/command-bridge'
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
            async with session.get('https://at-transport-ltd.com'+path,headers=service_headers('GET',path),allow_redirects=False) as response:
                if response.status!=200: raise RuntimeError('Hub identity lookup unavailable')
                payload=await response.json()
        fresh={}
        for row in payload.get('linkedDrivers',[]):
            uid=str(row.get('discordUserId',''))
            url=row.get('profileUrl','')
            if uid.isdigit() and row.get('displayName') and url==f'https://at-transport-ltd.com/drivers-hub/profile/{uid}':
                fresh[int(uid)]={'name':str(row['displayName'])[:100],'url':url}
        _link_cache=fresh; _link_loaded=time.monotonic()
    except (aiohttp.ClientError,RuntimeError,ValueError,TimeoutError):
        # Expired status is not asserted as linked when the website is unreachable.
        if time.monotonic()-_link_loaded>180: _link_cache={}

async def publish_careers(guild, builder):
    await refresh_hub_links(force=True)
    path='/api/integrations/driver-careers'
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        preferences={}
        async with session.get('https://at-transport-ltd.com'+path,headers=service_headers('GET',path),allow_redirects=False) as response:
            if response.status==200:
                content=await response.json()
                preferences={int(p['discordUserId']):p for p in content.get('preferences',[]) if str(p.get('discordUserId','')).isdigit()}
        profiles=[]
        for member in guild.members:
            if member.bot or not cached_hub_link(member.id): continue
            try:
                data=await builder(member,preferences.get(member.id))
                if data: profiles.append(data)
            except Exception as error:
                print(f'HUB CAREER SYNC RETRY: {member.id} {type(error).__name__}',flush=True)
        if not profiles: return
        # Bound batches keep old company snapshot limits and retry behaviour separate.
        for offset in range(0,len(profiles),5):
            body=json.dumps({'profiles':profiles[offset:offset+5]},separators=(',',':'))
            async with session.post('https://at-transport-ltd.com'+path,data=body.encode(),headers=service_headers('POST',path,body),allow_redirects=False) as response:
                if response.status not in (200,202): raise RuntimeError(f'Career bridge HTTP {response.status}')
        print(f'WEBSITE DRIVER CAREERS PUBLISHED profiles={len(profiles)}',flush=True)
