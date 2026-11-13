"""Separate Race career records and permission-free Discord role mirrors.

Only committed Trucky totals are accepted by the caller. This module never
writes mileage totals, Real career tables, or Company Mods access.
"""
from decimal import Decimal
import discord

SCHEMA = """
CREATE TABLE IF NOT EXISTS driver_race_role_map (
    guild_id BIGINT NOT NULL, kind TEXT NOT NULL, threshold BIGINT NOT NULL,
    role_id BIGINT NOT NULL, PRIMARY KEY(guild_id,kind,threshold)
);
CREATE TABLE IF NOT EXISTS driver_race_achievements (
    discord_user_id BIGINT NOT NULL, kind TEXT NOT NULL, threshold BIGINT NOT NULL,
    earned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(discord_user_id,kind,threshold)
);
"""


def targets(miles, ranks, achievements, hall):
    miles = Decimal(str(miles))
    result = set()
    if miles <= 0:
        return result
    for kind, entries in (("rank", ranks), ("hall", hall)):
        earned = [threshold for threshold, _, _ in entries if threshold <= miles]
        if earned:
            result.add((kind, max(earned)))
    result.update(("achievement", threshold) for threshold, _, _ in achievements if threshold <= miles)
    return result


async def ensure_roles(conn, guild, ranks, achievements, hall):
    """Serialised by caller; saved IDs own the mirrors, never existing Real roles."""
    mapped = {(r['kind'], r['threshold']): r['role_id'] for r in await conn.fetch(
        'SELECT kind,threshold,role_id FROM driver_race_role_map WHERE guild_id=$1', guild.id)}
    for kind, entries in (("rank", ranks), ("achievement", achievements), ("hall", hall)):
        for threshold, source_id, name in entries:
            key = (kind, threshold)
            if key in mapped and guild.get_role(mapped[key]):
                continue
            source = guild.get_role(source_id) if source_id else None
            label = f"Hall of Fame {name}" if kind == "hall" else name
            label = f"{label} • Race Mileage"
            # Adopt only a safe exact-name mirror after a crash between create/save.
            role = next((r for r in guild.roles if r.name == label and not r.managed
                         and r.permissions.value == 0), None)
            if role is None:
                role = await guild.create_role(name=label, permissions=discord.Permissions.none(),
                    colour=source.colour if source else discord.Colour.blue(),
                    hoist=False, mentionable=False, reason="A&T separate Trucky Race career")
            await conn.execute('''INSERT INTO driver_race_role_map(guild_id,kind,threshold,role_id)
                VALUES($1,$2,$3,$4) ON CONFLICT(guild_id,kind,threshold)
                DO UPDATE SET role_id=EXCLUDED.role_id''', guild.id, kind, threshold, role.id)
            mapped[key] = role.id
    return mapped


async def sync_driver(conn, guild, user_id, miles, mapped, ranks, achievements, hall):
    member = guild.get_member(user_id)
    if member is None:
        return False
    desired = targets(miles, ranks, achievements, hall)
    for kind, threshold in desired:
        if kind != 'rank':
            await conn.execute('''INSERT INTO driver_race_achievements(discord_user_id,kind,threshold)
                VALUES($1,$2,$3) ON CONFLICT DO NOTHING''', user_id, kind, threshold)
    saved = await conn.fetch('SELECT kind,threshold FROM driver_race_achievements WHERE discord_user_id=$1', user_id)
    desired.update((r['kind'], r['threshold']) for r in saved if r['kind'] == 'achievement')
    halls = [r['threshold'] for r in saved if r['kind'] == 'hall']
    if halls:
        desired = {key for key in desired if key[0] != 'hall'} | {('hall', max(halls))}
    owned = {role.id for role in member.roles}
    add = [guild.get_role(mapped[key]) for key in desired if key in mapped and mapped[key] not in owned]
    remove = [guild.get_role(role_id) for key, role_id in mapped.items()
              if key not in desired and key[0] in {'rank','hall'} and role_id in owned]
    if any(add):
        await member.add_roles(*(r for r in add if r), reason="Verified Trucky Race mileage career")
    if any(remove):
        await member.remove_roles(*(r for r in remove if r), reason="Updated Trucky Race rank")
    return True
