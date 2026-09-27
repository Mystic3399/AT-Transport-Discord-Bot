import os

import asyncpg
import discord
from discord.ext import commands


# --------------------------------------------------
# A&T TRANSPORT LTD - CONFIGURATION
# --------------------------------------------------

TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
GUILD_ID = int(os.getenv("GUILD_ID"))

# --------------------------------------------------
# A&T MILEAGE PROGRESSION ROLES
# --------------------------------------------------

PROGRESSION_ROLES = [
    (
        0,
        int(os.getenv("ROLE_NEW_DRIVER")),
        "A&T New Driver",
    ),
    (
        5000,
        int(os.getenv("ROLE_ROAD_RUNNER")),
        "Road Runner",
    ),
    (
        10000,
        int(os.getenv("ROLE_DISTANCE_DRIVER")),
        "Distance Driver",
    ),
    (
        15000,
        int(os.getenv("ROLE_AURORA_DRIVER")),
        "Aurora Driver",
    ),
    (
        20000,
        int(os.getenv("ROLE_WOLF_PACK_DRIVER")),
        "Wolf Pack Driver",
    ),
    (
        25000,
        int(os.getenv("ROLE_VIKING_HAULER")),
        "Viking Hauler",
    ),
    (
        30000,
        int(os.getenv("ROLE_ELITE_HAULER")),
        "Elite Hauler",
    ),
    (
        35000,
        int(os.getenv("ROLE_AT_VETERAN")),
        "A&T Veteran",
    ),
    (
        40000,
        int(os.getenv("ROLE_AT_ROAD_LEGEND")),
        "A&T Road Legend",
    ),
    (
        50000,
        int(os.getenv("ROLE_BEYOND_HORIZONS")),
        "Beyond Horizons",
    ),
]


# --------------------------------------------------
# TRUCKSBOOK -> DISCORD DRIVER MAPPINGS
# --------------------------------------------------

DRIVER_MAPPINGS = {
    "Mystical Custom": int(
        os.getenv("DRIVER_MYSTICAL_CUSTOM")
    ),
}

# --------------------------------------------------
# POSTGRESQL DATABASE
# --------------------------------------------------

db_pool = None


async def setup_database():
    global db_pool

    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL has not been configured."
        )

    print("Connecting to A&T PostgreSQL database...")

    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=5,
    )

    async with db_pool.acquire() as connection:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS driver_progress (
                discord_user_id BIGINT PRIMARY KEY,
                trucksbook_name TEXT NOT NULL UNIQUE,
                real_miles BIGINT NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_jobs (
                job_id TEXT PRIMARY KEY,
                discord_user_id BIGINT NOT NULL,
                trucksbook_name TEXT NOT NULL,
                accepted_distance INTEGER NOT NULL,
                statistics TEXT NOT NULL,
                processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )

    print("POSTGRESQL CONNECTED")
    print("Database tables ready.")

# --------------------------------------------------
# DISCORD INTENTS
# --------------------------------------------------

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
)


# --------------------------------------------------
# BOT STARTUP
# --------------------------------------------------

@bot.event
async def on_ready():
    global db_pool

    if db_pool is None:
        try:
            await setup_database()
        except Exception as error:
            print(f"DATABASE ERROR: {error}")

    print("--------------------------------")
    print("A&T Transport LTD Bot")
    print(f"Logged in as: {bot.user}")

    guild = bot.get_guild(GUILD_ID)

    if guild is None:
        print(
            "ERROR: A&T Transport LTD server "
            "could not be found."
        )
    else:
        print(f"Connected Server: {guild.name}")
        print("Checking A&T progression roles...")

        for miles, role_id, role_name in PROGRESSION_ROLES:
            role = guild.get_role(role_id)

            if role:
                print(
                    f"OK: {role_name} "
                    f"({miles:,}+ miles)"
                )
            else:
                print(
                    "ERROR: Could not find role: "
                    f"{role_name}"
                )

        print("--------------------------------")

    try:
        synced = await bot.tree.sync()

        print(
            f"Synced {len(synced)} "
            "slash command(s)."
        )

    except Exception as error:
        print(
            "Slash command sync failed: "
            f"{error}"
        )


# --------------------------------------------------
# TRUCKSBOOK WEBHOOK DETECTOR
#
# READ-ONLY TEST MODE
#
# This currently:
# - Detects TrucksBook jobs
# - Reads the job ID
# - Reads Real / Race classification
# - Reads accepted mileage
# - Matches TrucksBook driver to Discord member
# - Prints everything to Railway
#
# It DOES NOT:
# - Save mileage
# - Add roles
# - Remove roles
# --------------------------------------------------

@bot.event
async def on_message(message):
    # Ignore normal Discord messages.
    if message.webhook_id is None:
        await bot.process_commands(message)
        return

    # TrucksBook job messages should contain embeds.
    if not message.embeds:
        await bot.process_commands(message)
        return

    for embed in message.embeds:
        data = embed.to_dict()

        title = data.get("title", "")
        description = data.get("description", "")

        author_data = data.get("author", {})
        author = author_data.get("name", "")

        # ------------------------------------------
        # CHECK FOR TRUCKSBOOK DELIVERY
        # ------------------------------------------

        combined_text = f"{title}\n{description}"

        if "Job delivery #" not in combined_text:
            continue

        print("--------------------------------")
        print("TRUCKSBOOK WEBHOOK DETECTED")
        print(f"Driver: {author}")
        print(f"Title: {title}")
        print(f"Description: {description}")
        print("--------------------------------")

        # ------------------------------------------
        # EXTRACT JOB ID
        # ------------------------------------------

        job_id = title.replace(
            "Job delivery #",
            "",
        ).strip()

        # ------------------------------------------
        # READ TRUCKSBOOK DETAILS
        # ------------------------------------------

        statistics = None
        accepted_distance = None

        for field in data.get("fields", []):
            field_name = field.get("name", "")
            field_value = field.get("value", "")

            if field_name != "Details":
                continue

            for line in field_value.splitlines():
                clean_line = line.strip()

                # Accepted distance
                if clean_line.startswith(
                    "Accepted distance:"
                ):
                    distance_text = clean_line.replace(
                        "Accepted distance:",
                        "",
                    ).strip()

                    distance_parts = (
                        distance_text.split()
                    )

                    if distance_parts:
                        distance_number = (
                            distance_parts[0]
                            .replace(",", "")
                        )

                        try:
                            accepted_distance = int(
                                distance_number
                            )
                        except ValueError:
                            accepted_distance = None

                # TrucksBook classification
                if clean_line.startswith(
                    "Statistics:"
                ):
                    statistics = clean_line.replace(
                        "Statistics:",
                        "",
                    ).strip()

        # ------------------------------------------
        # PRINT PARSED JOB
        # ------------------------------------------

        print("A&T JOB PARSER")
        print(f"Job ID: {job_id}")
        print(f"Driver: {author}")
        print(f"Statistics: {statistics}")

        print(
            "Accepted Distance: "
            f"{accepted_distance}"
        )

        # ------------------------------------------
        # REAL / RACE CHECK
        # ------------------------------------------

        qualifying_job = (
            statistics == "Real"
            and accepted_distance is not None
        )

        if qualifying_job:
            print("QUALIFYING JOB: YES")

            print(
                "ACTION: Would add "
                f"{accepted_distance} miles "
                "to A&T progression."
            )

        else:
            print("QUALIFYING JOB: NO")

            print(
                "ACTION: Job would be ignored."
            )

        print("--------------------------------")

        # ------------------------------------------
        # MATCH TRUCKSBOOK DRIVER TO DISCORD
        # ------------------------------------------

        discord_user_id = DRIVER_MAPPINGS.get(
            author
        )

        if discord_user_id:
            guild = bot.get_guild(GUILD_ID)

            if guild:
                member = guild.get_member(
                    discord_user_id
                )
            else:
                member = None

            if member:
                print(
                    "MATCHED DISCORD MEMBER: "
                    f"{member}"
                )

                print(
                    "Discord User ID: "
                    f"{member.id}"
                )

            else:
                print(
                    "WARNING: Discord member "
                    "not found for "
                    f"{author}"
                )

        else:
            print(
                "WARNING: No Discord mapping "
                "exists for "
                f"{author}"
            )

        print("--------------------------------")

        # ------------------------------------------
        # PRINT RAW TRUCKSBOOK EMBED FIELDS
        # ------------------------------------------

        for field in data.get("fields", []):
            field_name = field.get(
                "name",
                "",
            )

            field_value = field.get(
                "value",
                "",
            )

            print(
                f"Field: {field_name} = "
                f"{field_value}"
            )

        print(
            "Discord Message ID: "
            f"{message.id}"
        )

        print(
            "Webhook ID: "
            f"{message.webhook_id}"
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
