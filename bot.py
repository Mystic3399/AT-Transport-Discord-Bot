import os
import discord
from discord.ext import commands


# --------------------------------------------------
# A&T TRANSPORT LTD - CONFIGURATION
# --------------------------------------------------

TOKEN = os.getenv("DISCORD_TOKEN")

GUILD_ID = int(os.getenv("GUILD_ID"))


# --------------------------------------------------
# A&T MILEAGE PROGRESSION ROLES
# --------------------------------------------------

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


# --------------------------------------------------
# TRUCKSBOOK -> DISCORD DRIVER MAPPINGS
# --------------------------------------------------

DRIVER_MAPPINGS = {
    "Mystical Custom": int(os.getenv("DRIVER_MYSTICAL_CUSTOM")),
}


# --------------------------------------------------
# DISCORD INTENTS
# --------------------------------------------------

intents = discord.Intents.default()
intents.members = True
intents.message_content = True


bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# --------------------------------------------------
# BOT STARTUP
# --------------------------------------------------

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


# --------------------------------------------------
# TRUCKSBOOK WEBHOOK DETECTOR
# READ-ONLY - DOES NOT MODIFY MILEAGE OR ROLES
# --------------------------------------------------

@bot.event
async def on_message(message):

    # Ignore normal messages.
    # We only want Discord webhook messages.
    if message.webhook_id is None:
        await bot.process_commands(message)
        return

    # Ignore webhook messages without embeds.
    if not message.embeds:
        await bot.process_commands(message)
        return

    for embed in message.embeds:

        data = embed.to_dict()

        title = data.get("title", "")
        description = data.get("description", "")
        author = data.get("author", {}).get("name", "")

        # Check whether this is a TrucksBook delivery.
        combined_text = f"{title}\n{description}"

        if "Job delivery #" not in combined_text:
            continue

        print("--------------------------------")
        print("TRUCKSBOOK WEBHOOK DETECTED")
        print(f"Driver: {author}")
        print(f"Title: {title}")
        print(f"Description: {description}")
# --------------------------------------------------
# PARSE TRUCKSBOOK JOB
# READ-ONLY - DOES NOT SAVE MILEAGE
# --------------------------------------------------

job_id = title.replace("Job delivery #", "").strip()

statistics = None
accepted_distance = None

for field in data.get("fields", []):
    field_name = field.get("name", "")
    field_value = field.get("value", "")

    if field_name == "Details":
        for line in field_value.splitlines():
            clean_line = line.strip()

            if clean_line.startswith("Accepted distance:"):
                distance_text = clean_line.replace(
                    "Accepted distance:", ""
                ).strip()

                distance_number = distance_text.split()[0]
                distance_number = distance_number.replace(",", "")

                try:
                    accepted_distance = int(distance_number)
                except ValueError:
                    accepted_distance = None

            if clean_line.startswith("Statistics:"):
                statistics = clean_line.replace(
                    "Statistics:", ""
                ).strip()

print("--------------------------------")
print("A&T JOB PARSER")

print(f"Job ID: {job_id}")
print(f"Driver: {author}")
print(f"Statistics: {statistics}")
print(f"Accepted Distance: {accepted_distance}")

if statistics == "Real" and accepted_distance is not None:
    print("QUALIFYING JOB: YES")
    print(
        f"ACTION: Would add {accepted_distance} miles "
        "to A&T progression."
    )
else:
    print("QUALIFYING JOB: NO")
    print("ACTION: Job would be ignored.")

print("--------------------------------")
        # ------------------------------------------
        # MATCH TRUCKSBOOK DRIVER TO DISCORD MEMBER
        # ------------------------------------------

        discord_user_id = DRIVER_MAPPINGS.get(author)

        if discord_user_id:
            guild = bot.get_guild(GUILD_ID)

            member = (
                guild.get_member(discord_user_id)
                if guild
                else None
            )

            if member:
                print(
                    f"MATCHED DISCORD MEMBER: {member}"
                )
                print(
                    f"Discord User ID: {member.id}"
                )
            else:
                print(
                    f"WARNING: Discord member not found for {author}"
                )

        else:
            print(
                f"WARNING: No Discord mapping exists for {author}"
            )

        # ------------------------------------------
        # PRINT TRUCKSBOOK EMBED INFORMATION
        # ------------------------------------------

        for field in data.get("fields", []):
            print(
                f"Field: {field.get('name')} = "
                f"{field.get('value')}"
            )

        print(
            f"Discord Message ID: {message.id}"
        )

        print(
            f"Webhook ID: {message.webhook_id}"
        )

        print("--------------------------------")

    await bot.process_commands(message)


# --------------------------------------------------
# START BOT
# --------------------------------------------------

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN has not been configured."
    )


bot.run(TOKEN)
