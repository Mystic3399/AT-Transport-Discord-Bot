"""Read-only company status monitoring. No mileage or registration writers."""
import asyncio
from datetime import datetime, timezone


def company_status(trucksbook, trucky):
    if "member" in (trucksbook, trucky):
        return "Active company driver"
    if trucksbook == trucky == "not_member":
        return "Former company driver"
    return "Company status unconfirmed"


async def inspect_driver(row, guild, roster, tb_lookup):
    user_id = int(row["discord_user_id"])
    # Fetch absence explicitly: a cache miss does not prove someone left Discord.
    try:
        member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        presence = "In server" if member else "Unknown"
    except Exception as error:
        presence = ("Not in server" if getattr(error, "status", None) == 404
                    else "Unknown (Discord lookup unavailable)")
    tb = "unknown"
    account = row.get("trucksbook_user_id")
    if account and not row.get("trucksbook_link_conflict"):
        try:
            profile = await tb_lookup(int(account))
            # Existing parser only establishes positive membership. A missing
            # name on a changed/private page is NOT authoritative departure.
            if profile.get("success") and profile.get("belongs_to_at") is True:
                tb = "member"
        except Exception:
            pass
    tr = "unknown"
    account = row.get("trucky_account_id")
    if account and roster is not None:
        tr = "member" if str(account) in roster else "not_member"
    return {"discord_user_id": user_id, "discord": presence,
            "trucksbook_user_id": row.get("trucksbook_user_id"),
            "trucksbook_name": row.get("trucksbook_name"),
            "registration_status": row.get("registration_status"),
            "trucksbook_link_conflict": bool(row.get("trucksbook_link_conflict")),
            "trucksbook": tb, "trucky": tr,
            "company": company_status(tb, tr),
            "checked_at": datetime.now(timezone.utc).isoformat()}


async def inspect_drivers(rows, guild, roster, tb_lookup):
    semaphore = asyncio.Semaphore(3)
    async def inspect(row):
        async with semaphore:
            return await inspect_driver(dict(row), guild, roster, tb_lookup)
    return await asyncio.gather(*(inspect(row) for row in rows))


def resolve_driver_query(query, guild, reports):
    """Resolve mentions/IDs or unique exact names; never guess between drivers."""
    import re
    text = query.strip()
    mention = re.fullmatch(r'<@!?(\d+)>', text)
    if mention or text.isascii() and text.isdecimal():
        value = int(mention.group(1) if mention else text)
        return value if value > 0 else None
    name = text.removeprefix('@').strip().casefold()
    if not name:
        return None
    matches = set()
    for member in guild.members:
        if not member.bot and any(str(value or '').casefold() == name for value in
                (member.name, member.display_name, getattr(member, 'global_name', None))):
            matches.add(member.id)
    for report in reports:
        if str(report.get('trucksbook_name') or '').casefold() == name:
            matches.add(report['discord_user_id'])
    if len(matches) > 1:
        raise ValueError('ambiguous_driver_name')
    return next(iter(matches), None)
