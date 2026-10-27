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
    import discord
    try:
        member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        presence = "In server" if member else "Unknown"
    except discord.NotFound:
        presence = "Not in server"
    except (discord.Forbidden, discord.HTTPException):
        presence = "Unknown (Discord lookup unavailable)"
    tb = "unknown"
    account = row.get("trucksbook_user_id")
    if account:
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
            "trucksbook": tb, "trucky": tr,
            "company": company_status(tb, tr),
            "checked_at": datetime.now(timezone.utc).isoformat()}


async def inspect_drivers(rows, guild, roster, tb_lookup):
    semaphore = asyncio.Semaphore(3)
    async def inspect(row):
        async with semaphore:
            return await inspect_driver(dict(row), guild, roster, tb_lookup)
    return await asyncio.gather(*(inspect(row) for row in rows))
