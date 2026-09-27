import os
import discord
from discord.ext import commands

# --------------------------------------------------
# A&T TRANSPORT LTD DISCORD BOT
# --------------------------------------------------

TOKEN = os.getenv("DISCORD_TOKEN")

GUILD_ID = int(os.getenv("GUILD_ID"))

PROGRESSION_ROLES = [
    (0, int(os.getenv("ROLE_NEW_DRIVER")), "A&T New Driver"),
    (5000, int(os.getenv("ROLE_ROAD_RUNNER")), "Road Runner"),
    (10000, int(os.getenv("ROLE_DISTANCE_DRIVER")), "Distance Driver"),
    (15000, int(os.getenv("ROLE_AURORA_DRIVER")), "Aurora Driver"),
    (20000, int(os.getenv("ROLE_WOLF_PACK_DRIVER")), "Wolf Pack Driver"),
    (25000, int(os.getenv("ROLE_VIKING_HAULER")), "Viking Hauler"),
    (30000, int(os.getenv("ROLE_ELITE_HAULER")), "Elite Hauler"),
    (35000, int(os.getenv("ROLE_AT_VETERAN")), "A&T Veteran"),
    (40000, int(os.getenv("ROLE_AT_ROAD_LEGEND")), "A&T Road Legend"),
    (50000, int(os.getenv("ROLE_BEYOND_HORIZONS")), "Beyond Horizons"),
]

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(
    command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print("--------------------------------")
    print("A&T Transport LTD Bot")
    print(f"Logged in as: {bot.user}")
    print(f"Bot ID: {bot.user.id}")
    print("--------------------------------")

    guild = bot.get_guild(GUILD_ID)

    if guild is None:
        print("ERROR: A&T Transport LTD server could not be found.")
    else:
        print(f"Connected Server: {guild.name}")
        print("Checking A&T progression roles...")

        for miles, role_id, role_name in PROGRESSION_ROLES:
            role = guild.get_role(role_id)

            if role:
                print(f"OK: {role_name} ({miles:,}+ miles)")
            else:
                print(f"ERROR: Could not find role: {role_name}")

        print("--------------------------------")

    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s).")
    except Exception as error:
        print(f"Slash command sync failed: {error}")



if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN has not been configured."
    )

bot.run(TOKEN)
