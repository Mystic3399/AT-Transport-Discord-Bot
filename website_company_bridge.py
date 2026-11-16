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
            for member in guild.members:
                if member.bot or membership.get(member.id, {}).get('company') == 'Former company driver':
                    continue
                profile = await read_profile(connection, member.id)
                if profile is None:
                    continue
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

def signed_payload(snapshot, secret, timestamp=None):
    body = json.dumps(snapshot,separators=(',',':'))
    timestamp = str(int(time.time()) if timestamp is None else timestamp)
    path = '/api/integrations/command-bridge'
    signature = hmac.new(secret.encode(),f'POST\n{path}\n{timestamp}\n{body}'.encode(),hashlib.sha256).hexdigest()
    return body, {'content-type':'application/json','x-at-timestamp':timestamp,'x-at-signature':signature}

async def publish(pool, guild, read_profile, membership):
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
