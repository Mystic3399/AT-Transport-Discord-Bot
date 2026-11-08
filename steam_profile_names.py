"""Display-only Steam names, always matched back to an already verified Steam ID."""
import asyncio
import re
from xml.etree import ElementTree
import aiohttp

SCHEMA = """
CREATE TABLE IF NOT EXISTS driver_card_steam_names (
    discord_user_id BIGINT PRIMARY KEY,
    steam_id64 TEXT NOT NULL,
    display_name TEXT NOT NULL,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def parse_profile_name(payload, expected_id):
    if len(payload) > 131072 or b'<!DOCTYPE' in payload.upper() or b'<!ENTITY' in payload.upper():
        return None
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return None
    if root.tag != 'profile' or root.findtext('steamID64') != str(expected_id):
        return None
    name = ' '.join((root.findtext('steamID') or '').split())
    return name[:128] if name else None


async def fetch_name(session, steam_id):
    if not re.fullmatch(r'[0-9]{17}', str(steam_id)):
        return None
    try:
        async with session.get(f'https://steamcommunity.com/profiles/{steam_id}/',
                               params={'xml': '1'}, allow_redirects=False) as response:
            if response.status != 200:
                return None
            return parse_profile_name(await response.content.read(131073), steam_id)
    except (aiohttp.ClientError, TimeoutError):
        return None


async def refresh(pool):
    async with pool.acquire() as conn:
        rows = await conn.fetch('SELECT discord_user_id,steam_id64 FROM unified_steam_links WHERE verified_at IS NOT NULL')
    semaphore = asyncio.Semaphore(3)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async def update(row):
            async with semaphore:
                name = await fetch_name(session, row['steam_id64'])
            if name is None:
                return 0
            async with pool.acquire() as conn:
                await conn.execute('''INSERT INTO driver_card_steam_names(discord_user_id,steam_id64,display_name)
                    SELECT $1,$2,$3 WHERE EXISTS(SELECT 1 FROM unified_steam_links
                        WHERE discord_user_id=$1 AND steam_id64=$2 AND verified_at IS NOT NULL)
                    ON CONFLICT(discord_user_id) DO UPDATE SET steam_id64=EXCLUDED.steam_id64,
                        display_name=EXCLUDED.display_name,checked_at=NOW()''',
                    row['discord_user_id'],str(row['steam_id64']),name)
            return 1
        return sum(await asyncio.gather(*(update(row) for row in rows)))
