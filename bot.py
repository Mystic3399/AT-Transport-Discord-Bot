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
DRIVER_MAPPINGS = {
    "Mystical Custom": int(os.getenv("DRIVER_MYSTICAL_CUSTOM")),
}
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

@bot.event
async def on_message(message):
    # Only inspect webhook messages
    if message.webhook_id is None:
        await bot.process_commands(message)
        return

    # Only inspect TrucksBook webhook embeds
    if not message.embeds:
        await bot.process_commands(message)
        return

    for embed in message.embeds:
        data = embed.to_dict()

        title = data.get("title", "")
        description = data.get("description", "")
        author = data.get("author", {}).get("name", "")

        # Ignore webhook messages that are not TrucksBook deliveries
        combined_text = f"{title}\n{description}"

        if "Job delivery #" not in combined_text:
            continue

        print("--------------------------------")
print("TRUCKSBOOK WEBHOOK DETECTED")
print(f"Driver: {author}")
print(f"Title: {title}")
print(f"Description: {description}")

discord_user_id = DRIVER_MAPPINGS.get(author)

if discord_user_id:
    guild = bot.get_guild(GUILD_ID)
    member = guild.get_member(discord_user_id) if guild else None

    if member:
        print(f"MATCHED DISCORD MEMBER: {member}")
        print(f"Discord User ID: {member.id}")
    else:
        print(f"WARNING: Discord member not found for {author}")
        
    else:
    print(f"WARNING: No Discord mapping exists for {author}")

# Print embed fields so we can see exactly what Discord receives
for field in data.get("fields", []):
    print(
        f"Field: {field.get('name')} = "
        f"{field.get('value')}"
    )

print(f"Discord Message ID: {message.id}")
print(f"Webhook ID: {message.webhook_id}")
print("--------------------------------")
    await bot.process_commands(message)

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN has not been configured."
    )

bot.run(TOKEN)
