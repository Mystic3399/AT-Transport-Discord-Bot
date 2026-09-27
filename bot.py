import os
import re

import aiohttp
import asyncpg
import discord
from bs4 import BeautifulSoup
from discord.ext import commands


# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
GUILD_ID = int(os.getenv("GUILD_ID"))


def optional_snowflake_env(variable_name):
    """Read an optional Discord ID without making bot startup fragile."""
    raw_value = os.getenv(variable_name, "").strip()

    if not raw_value:
        return None

    try:
        value = int(raw_value)
    except ValueError:
        print(
            f"CONFIGURATION ERROR: {variable_name} must be a valid "
            "Discord channel ID. Milestone announcements are disabled."
        )
        return None

    if value <= 0:
        print(
            f"CONFIGURATION ERROR: {variable_name} must be a valid "
            "Discord channel ID. Milestone announcements are disabled."
        )
        return None

    return value


MILESTONE_ANNOUNCEMENT_CHANNEL_ID = optional_snowflake_env(
    "MILESTONE_ANNOUNCEMENT_CHANNEL_ID"
)


# --------------------------------------------------
# ONBOARDING CONFIGURATION
# --------------------------------------------------

ONBOARDING_CHANNEL_ID = int(
    os.getenv(
        "ONBOARDING_CHANNEL_ID",
        "1553652697184473210",
    )
)

VERIFICATION_CATEGORY_ID = int(
    os.getenv(
        "VERIFICATION_CATEGORY_ID",
        "1553650713874407504",
    )
)

DRIVER_RECORDS_CATEGORY_ID = int(
    os.getenv(
        "DRIVER_RECORDS_CATEGORY_ID",
        "1553650853393727558",
    )
)

VERIFICATION_LOG_CHANNEL_ID = int(
    os.getenv(
        "VERIFICATION_LOG_CHANNEL_ID",
        "1553650941105020949",
    )
)

VERIFICATION_STAFF_ROLE_IDS = {
    int(role_id.strip())
    for role_id in os.getenv(
        "VERIFICATION_STAFF_ROLE_IDS",
        (
            "1493662151473369280,"
            "1494054587055870012,"
            "1542938047455432734,"
            "1553651916280561735"
        ),
    ).split(",")
    if role_id.strip()
}


# --------------------------------------------------
# A&T PROGRESSION ROLES
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
# TEMPORARY FALLBACK DRIVER MAPPING
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

    print(
        "Connecting to A&T PostgreSQL database..."
    )

    pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=5,
    )

    async with pool.acquire() as connection:

        # ------------------------------------------
        # DRIVER PROGRESSION
        # ------------------------------------------

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

        # ------------------------------------------
        # PROCESSED TRUCKSBOOK JOBS
        # ------------------------------------------

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

        # ------------------------------------------
        # ANNOUNCED PROGRESSION MILESTONES
        # ------------------------------------------

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS announced_milestones (
                discord_user_id BIGINT NOT NULL,
                milestone_miles BIGINT NOT NULL,
                trucksbook_name TEXT NOT NULL,
                job_id TEXT NOT NULL,
                recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                announced_at TIMESTAMPTZ,
                PRIMARY KEY (
                    discord_user_id,
                    milestone_miles
                )
            );
            """
        )

        # ------------------------------------------
        # DISCORD / TRUCKSBOOK DRIVER LINKS
        # ------------------------------------------

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS driver_links (
                trucksbook_name TEXT PRIMARY KEY,
                discord_user_id BIGINT NOT NULL UNIQUE,
                trucksbook_user_id BIGINT UNIQUE,
                linked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )

        # ------------------------------------------
        # DRIVER ONBOARDING / VERIFICATION
        # ------------------------------------------

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS driver_verifications (
                discord_user_id BIGINT PRIMARY KEY,
                verification_channel_id BIGINT UNIQUE,
                trucksbook_name TEXT,
                trucksbook_user_id BIGINT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                confirmed_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ
            );
            """
        )

        # ------------------------------------------
        # ONBOARDING INDEXES
        # ------------------------------------------

        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
                idx_driver_verifications_status
            ON driver_verifications(status);
            """
        )

        await connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                idx_driver_verifications_trucksbook_user
            ON driver_verifications(trucksbook_user_id)
            WHERE trucksbook_user_id IS NOT NULL
              AND status IN (
                  'pending',
                  'confirmed'
              );
            """
        )

    db_pool = pool

    print("POSTGRESQL CONNECTED")
    print("Database tables ready.")
    print("Driver onboarding tables ready.")


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
# BASIC HELPERS
# --------------------------------------------------

def extract_integer(value):
    if not value:
        return None

    match = re.search(
        r"[\d,]+",
        str(value),
    )

    if not match:
        return None

    try:
        return int(
            match.group(0).replace(",", "")
        )
    except ValueError:
        return None


def get_progression_role(real_miles):
    target = PROGRESSION_ROLES[0]

    for progression_role in PROGRESSION_ROLES:
        required_miles = progression_role[0]

        if real_miles >= required_miles:
            target = progression_role
        else:
            break

    return target


def get_next_progression_role(real_miles):
    """Return the next configured rank, or None at maximum rank."""
    for progression_role in PROGRESSION_ROLES:
        if real_miles < progression_role[0]:
            return progression_role

    return None


def build_progress_bar(real_miles, target_miles, width=20):
    """Build a fixed-width bar showing absolute progress to a target."""
    if target_miles <= 0:
        percentage = 100.0
    else:
        percentage = min(
            max(real_miles / target_miles * 100, 0.0),
            100.0,
        )

    filled = round(width * percentage / 100)
    bar = (
        "█" * filled
        + "░" * (width - filled)
    )

    return bar, percentage


# --------------------------------------------------
# DRIVER DATABASE HELPERS
# --------------------------------------------------

def normalise_trucksbook_name(trucksbook_name):
    """Return the stable name used to match profiles and webhooks."""
    name = " ".join((trucksbook_name or "").split())

    # Public profiles may add the VTC prefix while job webhooks omit it.
    return re.sub(
        r"^a\s*&\s*t(?:\s+|$)",
        "",
        name,
        count=1,
        flags=re.IGNORECASE,
    ).strip()


async def find_driver_link(
    trucksbook_name,
):
    if db_pool is None:
        return None

    normalised_name = normalise_trucksbook_name(
        trucksbook_name
    )

    if not normalised_name:
        return None

    async with db_pool.acquire() as connection:
        discord_user_id = (
            await connection.fetchval(
                """
                SELECT discord_user_id
                FROM driver_links
                WHERE LOWER(
                    REGEXP_REPLACE(
                        BTRIM(trucksbook_name),
                        '^a\\s*&\\s*t(\\s+|$)',
                        '',
                        'i'
                    )
                ) = LOWER($1);
                """,
                normalised_name,
            )
        )

    if discord_user_id:
        return int(discord_user_id)

    for mapped_name, mapped_user_id in (
        DRIVER_MAPPINGS.items()
    ):
        if (
            normalise_trucksbook_name(mapped_name).casefold()
            == normalised_name.casefold()
        ):
            return mapped_user_id

    return None


async def record_real_job(
    job_id,
    trucksbook_name,
    discord_user_id,
    accepted_distance,
):
    if db_pool is None:
        print(
            "DATABASE ERROR: Pool is not ready."
        )
        return None

    async with db_pool.acquire() as connection:
        async with connection.transaction():

            previous_real_miles = await connection.fetchval(
                """
                SELECT real_miles
                FROM driver_progress
                WHERE discord_user_id = $1
                FOR UPDATE;
                """,
                discord_user_id,
            )

            previous_real_miles = int(
                previous_real_miles or 0
            )

            inserted_job_id = (
                await connection.fetchval(
                    """
                    INSERT INTO processed_jobs (
                        job_id,
                        discord_user_id,
                        trucksbook_name,
                        accepted_distance,
                        statistics
                    )
                    VALUES (
                        $1,
                        $2,
                        $3,
                        $4,
                        'Real'
                    )
                    ON CONFLICT (job_id)
                    DO NOTHING
                    RETURNING job_id;
                    """,
                    job_id,
                    discord_user_id,
                    trucksbook_name,
                    accepted_distance,
                )
            )

            if inserted_job_id is None:
                return {
                    "status": "duplicate",
                    "real_miles": None,
                    "crossed_milestones": [],
                }

            await connection.execute(
                """
                INSERT INTO driver_progress (
                    discord_user_id,
                    trucksbook_name,
                    real_miles,
                    updated_at
                )
                VALUES (
                    $1,
                    $2,
                    $3,
                    NOW()
                )
                ON CONFLICT (discord_user_id)
                DO UPDATE SET
                    trucksbook_name =
                        EXCLUDED.trucksbook_name,
                    real_miles =
                        driver_progress.real_miles
                        + EXCLUDED.real_miles,
                    updated_at =
                        NOW();
                """,
                discord_user_id,
                trucksbook_name,
                accepted_distance,
            )

            real_miles = (
                await connection.fetchval(
                    """
                    SELECT real_miles
                    FROM driver_progress
                    WHERE discord_user_id = $1;
                    """,
                    discord_user_id,
                )
            )

            real_miles = int(real_miles)

            crossed_milestones = []

            for (
                milestone_miles,
                _,
                milestone_rank,
            ) in PROGRESSION_ROLES:
                if milestone_miles <= 0:
                    continue

                if not (
                    previous_real_miles
                    < milestone_miles
                    <= real_miles
                ):
                    continue

                inserted_milestone = await connection.fetchval(
                    """
                    INSERT INTO announced_milestones (
                        discord_user_id,
                        milestone_miles,
                        trucksbook_name,
                        job_id
                    )
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (
                        discord_user_id,
                        milestone_miles
                    )
                    DO NOTHING
                    RETURNING milestone_miles;
                    """,
                    discord_user_id,
                    milestone_miles,
                    trucksbook_name,
                    job_id,
                )

                if inserted_milestone is not None:
                    crossed_milestones.append(
                        {
                            "miles": int(inserted_milestone),
                            "rank": milestone_rank,
                        }
                    )

            return {
                "status": "added",
                "real_miles": real_miles,
                "previous_real_miles": previous_real_miles,
                "crossed_milestones": crossed_milestones,
            }


async def mark_milestone_announced(
    discord_user_id,
    milestone_miles,
):
    if db_pool is None:
        return False

    async with db_pool.acquire() as connection:
        result = await connection.execute(
            """
            UPDATE announced_milestones
            SET announced_at = NOW()
            WHERE discord_user_id = $1
              AND milestone_miles = $2
              AND announced_at IS NULL;
            """,
            discord_user_id,
            milestone_miles,
        )

    return result != "UPDATE 0"


def build_progression_milestone_embed(
    member,
    discord_user_id,
    trucksbook_name,
    milestone,
    real_miles,
):
    """Build a real milestone announcement embed."""
    milestone_miles = milestone["miles"]
    milestone_rank = milestone["rank"]
    next_role = get_next_progression_role(real_miles)
    driver_display = (
        member.mention
        if member is not None
        else f"<@{discord_user_id}>"
    )

    embed = discord.Embed(
        title="🏆 A&T TRANSPORT LTD — MILESTONE ACHIEVED",
        description=(
            f"Congratulations {driver_display}!\n\n"
            f"💎 **{milestone_miles:,} REAL MILES**\n"
            "A new A&T progression milestone has been reached."
        ),
        colour=discord.Colour.from_rgb(31, 78, 121),
    )

    if member is not None:
        embed.set_thumbnail(url=member.display_avatar.url)

    embed.add_field(
        name="Discord Driver",
        value=driver_display,
        inline=True,
    )
    embed.add_field(
        name="TrucksBook Driver",
        value=f"`{trucksbook_name}`",
        inline=True,
    )
    embed.add_field(
        name="New Progression Rank",
        value=f"🏅 **{milestone_rank}**",
        inline=False,
    )
    embed.add_field(
        name="Current Real Total",
        value=f"🚛 **{real_miles:,} Real miles**",
        inline=False,
    )

    if next_role is None:
        embed.add_field(
            name="Progression Status",
            value="🌌 **Beyond Horizons — maximum rank achieved**",
            inline=False,
        )
    else:
        next_miles, _, next_rank = next_role
        miles_remaining = max(next_miles - real_miles, 0)
        embed.add_field(
            name="Next Milestone",
            value=f"🌠 **{next_miles:,} — {next_rank}**",
            inline=True,
        )
        embed.add_field(
            name="Miles Remaining",
            value=f"📏 **{miles_remaining:,}**",
            inline=True,
        )

    embed.set_footer(
        text="A&T Transport LTD • Driven Beyond Horizons"
    )
    return embed


async def announce_progression_milestone(
    guild,
    member,
    discord_user_id,
    trucksbook_name,
    milestone,
    real_miles,
):
    """Send one newly recorded milestone without affecting saved mileage."""
    milestone_miles = milestone["miles"]
    milestone_rank = milestone["rank"]

    if MILESTONE_ANNOUNCEMENT_CHANNEL_ID is None:
        print(
            "MILESTONE ANNOUNCEMENT SKIPPED: "
            "MILESTONE_ANNOUNCEMENT_CHANNEL_ID is unset or invalid. "
            f"Recorded {milestone_miles:,} miles for {trucksbook_name}."
        )
        return

    channel = bot.get_channel(
        MILESTONE_ANNOUNCEMENT_CHANNEL_ID
    )

    if channel is None:
        try:
            channel = await bot.fetch_channel(
                MILESTONE_ANNOUNCEMENT_CHANNEL_ID
            )
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ) as error:
            print(
                "MILESTONE ANNOUNCEMENT SKIPPED: Could not access "
                f"channel {MILESTONE_ANNOUNCEMENT_CHANNEL_ID}: {error}"
            )
            return

    if guild is not None and getattr(channel, "guild", None) != guild:
        print(
            "MILESTONE ANNOUNCEMENT SKIPPED: Configured channel is "
            "not in the A&T Transport LTD server."
        )
        return

    embed = build_progression_milestone_embed(
        member,
        discord_user_id,
        trucksbook_name,
        milestone,
        real_miles,
    )

    try:
        await channel.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException) as error:
        print(
            "MILESTONE ANNOUNCEMENT ERROR: Mileage remains saved; "
            f"Discord send failed for {trucksbook_name} at "
            f"{milestone_miles:,} miles: {error}"
        )
        return

    try:
        await mark_milestone_announced(
            discord_user_id,
            milestone_miles,
        )
    except Exception as error:
        print(
            "MILESTONE DATABASE WARNING: Announcement was sent, but "
            f"announced_at could not be updated: {error}"
        )

    print(
        "MILESTONE ANNOUNCED: "
        f"{trucksbook_name} reached {milestone_miles:,} Real miles "
        f"and earned {milestone_rank}."
    )


# --------------------------------------------------
# ONBOARDING DATABASE HELPERS
# --------------------------------------------------

async def get_verification_by_user(
    discord_user_id,
):
    if db_pool is None:
        return None

    async with db_pool.acquire() as connection:
        return await connection.fetchrow(
            """
            SELECT
                discord_user_id,
                verification_channel_id,
                trucksbook_name,
                trucksbook_user_id,
                status,
                created_at,
                confirmed_at,
                completed_at
            FROM driver_verifications
            WHERE discord_user_id = $1;
            """,
            discord_user_id,
        )


async def get_verification_by_channel(
    channel_id,
):
    if db_pool is None:
        return None

    async with db_pool.acquire() as connection:
        return await connection.fetchrow(
            """
            SELECT
                discord_user_id,
                verification_channel_id,
                trucksbook_name,
                trucksbook_user_id,
                status,
                created_at,
                confirmed_at,
                completed_at
            FROM driver_verifications
            WHERE verification_channel_id = $1;
            """,
            channel_id,
        )


async def driver_already_linked(
    discord_user_id,
):
    if db_pool is None:
        return False

    async with db_pool.acquire() as connection:
        existing = await connection.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM driver_links
                WHERE discord_user_id = $1
            );
            """,
            discord_user_id,
        )

    return bool(existing)


# --------------------------------------------------
# PROGRESSION ROLE MANAGEMENT
# --------------------------------------------------

async def sync_member_progression_role(
    guild,
    discord_user_id,
    real_miles,
):
    member = guild.get_member(
        discord_user_id
    )

    if member is None:
        try:
            member = await guild.fetch_member(
                discord_user_id
            )
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            return {
                "status": "member_missing",
                "role_name": None,
            }

    (
        required_miles,
        target_role_id,
        target_role_name,
    ) = get_progression_role(
        real_miles
    )

    target_role = guild.get_role(
        target_role_id
    )

    if target_role is None:
        return {
            "status": "role_missing",
            "role_name": target_role_name,
        }

    progression_role_ids = {
        role_id
        for (
            _,
            role_id,
            _,
        ) in PROGRESSION_ROLES
    }

    roles_to_remove = [
        role
        for role in member.roles
        if (
            role.id in progression_role_ids
            and role.id != target_role_id
        )
    ]

    changed = False

    if roles_to_remove:
        await member.remove_roles(
            *roles_to_remove,
            reason=(
                "A&T automatic mileage "
                "progression update"
            ),
        )

        changed = True

    if target_role not in member.roles:
        await member.add_roles(
            target_role,
            reason=(
                "A&T automatic mileage "
                "progression update"
            ),
        )

        changed = True

    if changed:
        return {
            "status": "updated",
            "role_name": target_role_name,
            "required_miles": required_miles,
        }

    return {
        "status": "correct",
        "role_name": target_role_name,
        "required_miles": required_miles,
    }


async def sync_all_driver_roles(
    guild,
):
    if db_pool is None:
        print(
            "ROLE SYNC ERROR: "
            "Database unavailable."
        )
        return

    async with db_pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT
                discord_user_id,
                trucksbook_name,
                real_miles
            FROM driver_progress
            ORDER BY real_miles DESC;
            """
        )

    print("--------------------------------")
    print("Synchronising driver roles...")

    for row in rows:
        discord_user_id = int(
            row["discord_user_id"]
        )

        trucksbook_name = row[
            "trucksbook_name"
        ]

        real_miles = int(
            row["real_miles"]
        )

        try:
            result = (
                await sync_member_progression_role(
                    guild,
                    discord_user_id,
                    real_miles,
                )
            )

            status = result["status"]
            role_name = result["role_name"]

            if status == "updated":
                print(
                    f"ROLE UPDATED: "
                    f"{trucksbook_name} -> "
                    f"{role_name} "
                    f"({real_miles:,} miles)"
                )

            elif status == "correct":
                print(
                    f"ROLE OK: "
                    f"{trucksbook_name} -> "
                    f"{role_name} "
                    f"({real_miles:,} miles)"
                )

            elif status == "member_missing":
                print(
                    f"ROLE SKIPPED: "
                    f"{trucksbook_name} "
                    "is not currently in Discord."
                )

            elif status == "role_missing":
                print(
                    f"ROLE ERROR: "
                    f"{role_name} "
                    "could not be found."
                )

        except discord.Forbidden:
            print(
                f"ROLE ERROR: No permission "
                f"to manage roles for "
                f"{trucksbook_name}."
            )

        except discord.HTTPException as error:
            print(
                f"ROLE ERROR: "
                f"{trucksbook_name}: "
                f"{error}"
            )

    print("Driver role sync complete.")
    print("--------------------------------")


# --------------------------------------------------
# ONBOARDING CONFIGURATION CHECK
# --------------------------------------------------

def check_onboarding_configuration(
    guild,
):
    print("--------------------------------")
    print("Checking A&T onboarding configuration...")

    onboarding_channel = guild.get_channel(
        ONBOARDING_CHANNEL_ID
    )

    verification_category = guild.get_channel(
        VERIFICATION_CATEGORY_ID
    )

    records_category = guild.get_channel(
        DRIVER_RECORDS_CATEGORY_ID
    )

    log_channel = guild.get_channel(
        VERIFICATION_LOG_CHANNEL_ID
    )

    if onboarding_channel:
        print(
            "OK: Driver onboarding channel found."
        )
    else:
        print(
            "ERROR: Driver onboarding channel "
            "could not be found."
        )

    if verification_category:
        print(
            "OK: Driver verification category found."
        )
    else:
        print(
            "ERROR: Driver verification category "
            "could not be found."
        )

    if records_category:
        print(
            "OK: Driver records category found."
        )
    else:
        print(
            "ERROR: Driver records category "
            "could not be found."
        )

    if log_channel:
        print(
            "OK: Verification log channel found."
        )
    else:
        print(
            "ERROR: Verification log channel "
            "could not be found."
        )

    for role_id in VERIFICATION_STAFF_ROLE_IDS:
        role = guild.get_role(
            role_id
        )

        if role:
            print(
                f"OK: Verification staff role: "
                f"{role.name}"
            )
        else:
            print(
                "ERROR: Verification staff role "
                f"{role_id} could not be found."
            )

    print(
        "A&T onboarding configuration check complete."
    )
    print("--------------------------------")


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
            print(
                f"DATABASE ERROR: {error}"
            )

    print("--------------------------------")
    print("A&T Transport LTD Bot")
    print(f"Logged in as: {bot.user}")
    print(f"Bot ID: {bot.user.id}")
    print("--------------------------------")

    guild = bot.get_guild(
        GUILD_ID
    )

    if guild is None:
        print(
            "ERROR: A&T Transport LTD server "
            "could not be found."
        )
    else:
        print(
            f"Connected Server: "
            f"{guild.name}"
        )

        print(
            "Checking A&T progression roles..."
        )

        all_roles_found = True

        for (
            miles,
            role_id,
            role_name,
        ) in PROGRESSION_ROLES:
            role = guild.get_role(
                role_id
            )

            if role:
                print(
                    f"OK: {role_name} "
                    f"({miles:,}+ miles)"
                )
            else:
                all_roles_found = False

                print(
                    "ERROR: Could not find role: "
                    f"{role_name}"
                )

        print("--------------------------------")

        if (
            db_pool is not None
            and all_roles_found
        ):
            await sync_all_driver_roles(
                guild
            )
        else:
            print(
                "Automatic role sync skipped."
            )

        check_onboarding_configuration(
            guild
        )

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
# TRUCKSBOOK WEBHOOK PROCESSING
# --------------------------------------------------

@bot.event
async def on_message(message):
    if message.webhook_id is None:
        await bot.process_commands(
            message
        )
        return

    if not message.embeds:
        await bot.process_commands(
            message
        )
        return

    for embed in message.embeds:
        data = embed.to_dict()

        title = (
            data.get("title")
            or ""
        )

        description = (
            data.get("description")
            or ""
        )

        author = (
            data.get("author")
            or {}
        )

        trucksbook_name = (
            author.get("name")
            or ""
        ).strip()

        combined_text = (
            f"{title}\n{description}"
        )

        if (
            "Job delivery #"
            not in combined_text
        ):
            continue

        # ------------------------------------------
        # JOB ID
        # ------------------------------------------

        job_match = re.search(
            r"Job delivery #(\d+)",
            combined_text,
            re.IGNORECASE,
        )

        if not job_match:
            print(
                "TRUCKSBOOK: "
                "Could not find Job ID."
            )
            continue

        job_id = job_match.group(1)

        # ------------------------------------------
        # DETAILS FIELD
        # ------------------------------------------

        details = ""

        for field in (
            data.get("fields")
            or []
        ):
            field_name = (
                field.get("name")
                or ""
            )

            if (
                field_name.strip().lower()
                == "details"
            ):
                details = (
                    field.get("value")
                    or ""
                )
                break

        if not details:
            print(
                f"TRUCKSBOOK JOB {job_id}: "
                "Details field missing."
            )
            continue

        statistics = None
        accepted_distance = None

        for line in details.splitlines():
            clean_line = (
                line.replace(
                    "**",
                    "",
                )
                .strip()
            )

            lower_line = (
                clean_line.lower()
            )

            if lower_line.startswith(
                "statistics:"
            ):
                statistics = (
                    clean_line
                    .split(
                        ":",
                        1,
                    )[1]
                    .strip()
                )

            elif lower_line.startswith(
                "accepted distance:"
            ):
                distance_text = (
                    clean_line
                    .split(
                        ":",
                        1,
                    )[1]
                    .strip()
                )

                accepted_distance = (
                    extract_integer(
                        distance_text
                    )
                )

        # ------------------------------------------
        # LOG JOB
        # ------------------------------------------

        print("--------------------------------")
        print(
            f"TRUCKSBOOK JOB: {job_id}"
        )
        print(
            f"Driver: {trucksbook_name}"
        )
        print(
            f"Statistics: {statistics}"
        )
        print(
            "Accepted Distance: "
            f"{accepted_distance}"
        )

        # ------------------------------------------
        # ONLY COUNT REAL JOBS
        # ------------------------------------------

        if (
            not statistics
            or statistics.lower()
            != "real"
        ):
            print(
                "ACTION: Job ignored. "
                "Not Real mileage."
            )
            print("--------------------------------")
            continue

        if (
            accepted_distance is None
            or accepted_distance < 0
        ):
            print(
                "ACTION: Job ignored. "
                "Invalid accepted distance."
            )
            print("--------------------------------")
            continue

        if not trucksbook_name:
            print(
                "ACTION: Job ignored. "
                "Driver name missing."
            )
            print("--------------------------------")
            continue

        # ------------------------------------------
        # FIND DRIVER
        # ------------------------------------------

        discord_user_id = (
            await find_driver_link(
                trucksbook_name
            )
        )

        if discord_user_id is None:
            print(
                "ACTION: Job ignored. "
                "No Discord link exists for "
                f"{trucksbook_name}."
            )
            print("--------------------------------")
            continue

        guild = bot.get_guild(
            GUILD_ID
        )

        member = None

        if guild is not None:
            member = guild.get_member(
                discord_user_id
            )

        if member:
            print(
                "MATCHED DISCORD MEMBER: "
                f"{member}"
            )
        else:
            print(
                "MATCHED DISCORD USER ID: "
                f"{discord_user_id}"
            )

        # ------------------------------------------
        # SAVE REAL JOB
        # ------------------------------------------

        try:
            result = await record_real_job(
                job_id=job_id,
                trucksbook_name=(
                    trucksbook_name
                ),
                discord_user_id=(
                    discord_user_id
                ),
                accepted_distance=(
                    accepted_distance
                ),
            )

        except Exception as error:
            print(
                "DATABASE JOB ERROR: "
                f"{error}"
            )
            print("--------------------------------")
            continue

        if result is None:
            print(
                "ACTION: Database unavailable."
            )

        elif (
            result["status"]
            == "duplicate"
        ):
            print(
                "ACTION: Duplicate Job ID. "
                "Mileage was not added again."
            )

        elif (
            result["status"]
            == "added"
        ):
            real_miles = result[
                "real_miles"
            ]

            print(
                "ACTION: Real mileage added."
            )

            print(
                f"NEW A&T TOTAL: "
                f"{real_miles:,} miles"
            )

            # --------------------------------------
            # UPDATE PROGRESSION ROLE
            # --------------------------------------

            if guild is None:
                print(
                    "ROLE ACTION: Server "
                    "unavailable."
                )
            else:
                try:
                    role_result = (
                        await sync_member_progression_role(
                            guild,
                            discord_user_id,
                            real_miles,
                        )
                    )

                    role_status = (
                        role_result["status"]
                    )

                    role_name = (
                        role_result["role_name"]
                    )

                    if (
                        role_status
                        == "updated"
                    ):
                        print(
                            "ROLE ACTION: "
                            f"Updated to "
                            f"{role_name}."
                        )

                    elif (
                        role_status
                        == "correct"
                    ):
                        print(
                            "ROLE ACTION: "
                            f"Already "
                            f"{role_name}."
                        )

                    elif (
                        role_status
                        == "member_missing"
                    ):
                        print(
                            "ROLE ACTION: "
                            "Discord member "
                            "not found."
                        )

                    elif (
                        role_status
                        == "role_missing"
                    ):
                        print(
                            "ROLE ACTION: "
                            f"{role_name} "
                            "was not found."
                        )

                except discord.Forbidden:
                    print(
                        "ROLE ACTION ERROR: "
                        "Bot does not have "
                        "permission to manage "
                        "this member's roles."
                    )

                except discord.HTTPException as error:
                    print(
                        "ROLE ACTION ERROR: "
                        f"{error}"
                    )

            # --------------------------------------
            # ANNOUNCE NEWLY CROSSED MILESTONES
            # --------------------------------------

            for milestone in result[
                "crossed_milestones"
            ]:
                try:
                    await announce_progression_milestone(
                        guild=guild,
                        member=member,
                        discord_user_id=discord_user_id,
                        trucksbook_name=trucksbook_name,
                        milestone=milestone,
                        real_miles=real_miles,
                    )
                except Exception as error:
                    print(
                        "MILESTONE ANNOUNCEMENT ERROR: Mileage remains "
                        f"saved; unexpected failure: {error}"
                    )

        print("--------------------------------")

    await bot.process_commands(
        message
    )

# --------------------------------------------------
# DRIVER ONBOARDING SYSTEM
# --------------------------------------------------

def member_is_verification_staff(member):
    return any(
        role.id in VERIFICATION_STAFF_ROLE_IDS
        for role in member.roles
    )


def safe_channel_name(member):
    name = member.display_name.lower()

    name = re.sub(
        r"[^a-z0-9-]+",
        "-",
        name,
    )

    name = re.sub(
        r"-+",
        "-",
        name,
    ).strip("-")

    if not name:
        name = str(member.id)

    return f"verify-{name[:70]}"


async def log_verification(
    guild,
    title,
    description,
):
    channel = guild.get_channel(
        VERIFICATION_LOG_CHANNEL_ID
    )

    if channel is None:
        print(
            "VERIFICATION LOG ERROR: "
            "Log channel not found."
        )
        return

    embed = discord.Embed(
        title=title,
        description=description,
        colour=discord.Colour.blue(),
    )

    try:
        await channel.send(
            embed=embed
        )
    except discord.HTTPException as error:
        print(
            "VERIFICATION LOG ERROR: "
            f"{error}"
        )


async def save_new_verification(
    discord_user_id,
    channel_id,
):
    if db_pool is None:
        return False

    async with db_pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO driver_verifications (
                discord_user_id,
                verification_channel_id,
                status,
                created_at,
                confirmed_at,
                completed_at
            )
            VALUES (
                $1,
                $2,
                'pending',
                NOW(),
                NULL,
                NULL
            )
            ON CONFLICT (discord_user_id)
            DO UPDATE SET
                verification_channel_id =
                    EXCLUDED.verification_channel_id,
                trucksbook_name = NULL,
                trucksbook_user_id = NULL,
                status = 'pending',
                created_at = NOW(),
                confirmed_at = NULL,
                completed_at = NULL;
            """,
            discord_user_id,
            channel_id,
        )

    return True

async def fetch_trucksbook_profile(
    trucksbook_user_id,
):
    profile_url = (
        "https://trucksbook.eu/profile/"
        f"{trucksbook_user_id}"
    )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "en-GB,en;q=0.9",
    }

    timeout = aiohttp.ClientTimeout(
        total=20
    )

    try:
        async with aiohttp.ClientSession(
            timeout=timeout,
            headers=headers,
        ) as session:

            async with session.get(
                profile_url,
                allow_redirects=True,
            ) as response:

                status_code = response.status

                html = await response.text(
                    errors="replace"
                )

    except Exception as error:
        print(
            "TRUCKSBOOK PROFILE REQUEST ERROR: "
            f"{error}"
        )

        return {
            "success": False,
            "reason": "request_failed",
        }

    if status_code != 200:
        return {
            "success": False,
            "reason": "http_error",
            "status_code": status_code,
        }

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    for unwanted in soup(
        [
            "script",
            "style",
            "noscript",
        ]
    ):
        unwanted.decompose()

    page_text = soup.get_text(
        "\n",
        strip=True,
    )

    lines = [
        line.strip()
        for line in page_text.splitlines()
        if line.strip()
    ]

    # ------------------------------------------
    # FIND PROFILE HEADER
    # ------------------------------------------

    profile_index = None

    profile_pattern = re.compile(
        rf"User\s+Profile\s*#\s*"
        rf"{re.escape(str(trucksbook_user_id))}",
        re.IGNORECASE,
    )

    for index, line in enumerate(lines):
        if profile_pattern.search(line):
            profile_index = index
            break

    if profile_index is None:
        return {
            "success": False,
            "reason": "profile_not_found",
        }

    # ------------------------------------------
    # EXTRACT PROFILE INFORMATION
    # ------------------------------------------

    profile_lines = lines[
        profile_index:
        profile_index + 30
    ]

    trucksbook_name = None
    company_name = None
    company_position = None

    # The first useful line after
    # "User Profile #123456" is the profile name.
    for line in profile_lines[1:]:
        lower_line = line.lower()

        if lower_line.startswith(
            (
                "followers",
                "following",
            )
        ):
            continue

        if (
            "a & t transport ltd"
            in lower_line
        ):
            continue

        trucksbook_name = line
        break

    # ------------------------------------------
    # FIND A&T COMPANY MEMBERSHIP
    # ------------------------------------------

    for line in profile_lines:
        if (
            "a & t transport ltd"
            not in line.lower()
        ):
            continue

        company_line = line.strip()

        company_match = re.match(
            r"^(A\s*&\s*T\s+Transport\s+LTD)"
            r"\s*-\s*(.+)$",
            company_line,
            re.IGNORECASE,
        )

        if company_match:
            company_name = (
                company_match
                .group(1)
                .strip()
            )

            company_position = (
                company_match
                .group(2)
                .strip()
            )

        else:
            company_name = (
                "A & T Transport LTD"
            )

        break

    belongs_to_at = (
        company_name is not None
        and company_name.lower()
        == "a & t transport ltd"
    )

    # ------------------------------------------
    # RESULT
    # ------------------------------------------

    print("--------------------------------")
    print("TRUCKSBOOK PROFILE VERIFIED")
    print(
        f"Profile ID: "
        f"{trucksbook_user_id}"
    )
    print(
        f"Driver: "
        f"{trucksbook_name}"
    )
    print(
        f"Company: "
        f"{company_name}"
    )
    print(
        f"Position: "
        f"{company_position}"
    )
    print(
        f"A&T Member: "
        f"{belongs_to_at}"
    )
    print("--------------------------------")

    return {
        "success": True,
        "trucksbook_user_id": int(
            trucksbook_user_id
        ),
        "trucksbook_name": trucksbook_name,
        "company_name": company_name,
        "company_position": company_position,
        "belongs_to_at": belongs_to_at,
    }



async def save_trucksbook_id(
    discord_user_id,
    trucksbook_user_id,
):
    if db_pool is None:
        return False

    async with db_pool.acquire() as connection:

        already_linked = await connection.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM driver_links
                WHERE trucksbook_user_id = $1
            );
            """,
            trucksbook_user_id,
        )

        if already_linked:
            return "already_linked"

        pending_owner = await connection.fetchval(
            """
            SELECT discord_user_id
            FROM driver_verifications
            WHERE trucksbook_user_id = $1
              AND discord_user_id != $2
              AND status IN (
                  'pending',
                  'confirmed'
              );
            """,
            trucksbook_user_id,
            discord_user_id,
        )

        if pending_owner:
            return "already_pending"

        result = await connection.execute(
            """
            UPDATE driver_verifications
            SET
                trucksbook_user_id = $1
            WHERE discord_user_id = $2
              AND status = 'pending';
            """,
            trucksbook_user_id,
            discord_user_id,
        )

    if result == "UPDATE 0":
        return False

    return True


async def cancel_verification_record(
    discord_user_id,
):
    if db_pool is None:
        return False

    async with db_pool.acquire() as connection:
        result = await connection.execute(
            """
            UPDATE driver_verifications
            SET
                status = 'cancelled',
                completed_at = NOW()
            WHERE discord_user_id = $1
              AND status IN (
                  'pending',
                  'confirmed'
              );
            """,
            discord_user_id,
        )

    return result != "UPDATE 0"


# --------------------------------------------------
# A&T DRIVER PROFILE
# --------------------------------------------------

@bot.command(
    name="profile"
)
async def driver_profile(
    ctx,
    member: discord.Member = None,
):
    """Display a linked driver's read-only A&T progression profile."""
    if ctx.guild is None:
        await ctx.reply(
            (
                "The A&T driver profile command can "
                "only be used inside the server."
            ),
            mention_author=False,
        )
        return

    if ctx.guild.id != GUILD_ID:
        return

    target_member = member or ctx.author

    if db_pool is None:
        await ctx.reply(
            (
                "❌ The A&T driver database is "
                "currently unavailable. Please try "
                "again shortly."
            ),
            mention_author=False,
        )
        return

    try:
        async with db_pool.acquire() as connection:
            profile = await connection.fetchrow(
                """
                SELECT
                    links.trucksbook_name,
                    COALESCE(progress.real_miles, 0)
                        AS real_miles
                FROM driver_links AS links
                LEFT JOIN driver_progress AS progress
                    ON progress.discord_user_id =
                        links.discord_user_id
                WHERE links.discord_user_id = $1;
                """,
                target_member.id,
            )

    except Exception as error:
        print(
            "PROFILE DATABASE ERROR: "
            f"{error}"
        )

        await ctx.reply(
            (
                "❌ The A&T driver database could "
                "not be reached. Please try again "
                "shortly."
            ),
            mention_author=False,
        )
        return

    if profile is None:
        if target_member.id == ctx.author.id:
            message = (
                "❌ Your Discord account is not "
                "linked to an A&T TrucksBook "
                "driver profile. Please complete "
                "driver verification first."
            )
        else:
            message = (
                f"❌ {target_member.mention} is not "
                "linked to an A&T TrucksBook "
                "driver profile."
            )

        await ctx.reply(
            message,
            mention_author=False,
        )
        return

    trucksbook_name = profile[
        "trucksbook_name"
    ]
    real_miles = max(
        int(profile["real_miles"]),
        0,
    )

    (
        _,
        _,
        current_rank_name,
    ) = get_progression_role(
        real_miles
    )

    next_role = get_next_progression_role(
        real_miles
    )

    embed = discord.Embed(
        title=(
            "🌌 A&T TRANSPORT LTD — "
            "DRIVER PROFILE"
        ),
        description=(
            f"Progression record for "
            f"{target_member.mention}"
        ),
        colour=discord.Colour.from_rgb(
            31,
            78,
            121,
        ),
    )

    embed.set_thumbnail(
        url=target_member.display_avatar.url
    )

    embed.add_field(
        name="Discord Driver",
        value=target_member.mention,
        inline=True,
    )

    embed.add_field(
        name="TrucksBook Driver",
        value=f"`{trucksbook_name}`",
        inline=True,
    )

    embed.add_field(
        name="Current Progression Rank",
        value=f"🏅 **{current_rank_name}**",
        inline=False,
    )

    embed.add_field(
        name="Real Miles",
        value=f"🚛 **{real_miles:,}**",
        inline=True,
    )

    if next_role is None:
        bar, _ = build_progress_bar(
            real_miles,
            PROGRESSION_ROLES[-1][0],
        )

        embed.add_field(
            name="Progression Status",
            value=(
                "🌌 **Beyond Horizons achieved**\n"
                f"`{bar}` **100%**\n"
                "Maximum A&T progression rank reached."
            ),
            inline=False,
        )

        embed.add_field(
            name="Miles Remaining",
            value="**0 — maximum rank achieved**",
            inline=False,
        )

    else:
        (
            next_required_miles,
            _,
            next_rank_name,
        ) = next_role

        miles_remaining = max(
            next_required_miles - real_miles,
            0,
        )

        bar, percentage = build_progress_bar(
            real_miles,
            next_required_miles,
        )

        embed.add_field(
            name="Next Progression Rank",
            value=f"🌠 **{next_rank_name}**",
            inline=True,
        )

        embed.add_field(
            name="Miles Remaining",
            value=f"**{miles_remaining:,}**",
            inline=True,
        )

        embed.add_field(
            name="Progress",
            value=(
                f"`{bar}` **{percentage:.0f}%**\n"
                f"**{real_miles:,} / "
                f"{next_required_miles:,} Real miles**"
            ),
            inline=False,
        )

    embed.set_footer(
        text=(
            "A&T Transport LTD • "
            "Driven Beyond Horizons"
        )
    )

    await ctx.send(
        embed=embed
    )


@driver_profile.error
async def driver_profile_error(
    ctx,
    error,
):
    if isinstance(
        error,
        commands.MemberNotFound,
    ):
        await ctx.reply(
            (
                "❌ I could not find that Discord "
                "member. Mention a current server "
                "member, for example: "
                "`!profile @Driver`"
            ),
            mention_author=False,
        )
        return

    print(
        "PROFILE COMMAND ERROR: "
        f"{error}"
    )

    await ctx.reply(
        (
            "❌ I could not display that driver "
            "profile. Please try again shortly."
        ),
        mention_author=False,
    )

@bot.command(
    name="verify"
)
async def verify_driver(ctx):
    # Command must be used in the permanent
    # driver onboarding channel.

    if ctx.guild is None:
        return

    if ctx.guild.id != GUILD_ID:
        return

    if ctx.channel.id != ONBOARDING_CHANNEL_ID:
        await ctx.reply(
            "Please use the A&T driver onboarding "
            "channel to start verification.",
            mention_author=False,
        )
        return

    member = ctx.author

    if member.bot:
        return

    if db_pool is None:
        await ctx.reply(
            "The A&T verification database is "
            "currently unavailable. Please try "
            "again shortly.",
            mention_author=False,
        )
        return

    # ------------------------------------------
    # CHECK FOR EXISTING DRIVER LINK
    # ------------------------------------------

    if await driver_already_linked(
        member.id
    ):
        await ctx.reply(
            (
                f"{member.mention}, your Discord "
                "account is already linked to an "
                "A&T TrucksBook driver profile."
            ),
            mention_author=False,
        )
        return

    # ------------------------------------------
    # CHECK EXISTING VERIFICATION
    # ------------------------------------------

    existing = await get_verification_by_user(
        member.id
    )

    if (
        existing
        and existing["status"]
        in (
            "pending",
            "confirmed",
        )
    ):
        existing_channel_id = (
            existing[
                "verification_channel_id"
            ]
        )

        if existing_channel_id:
            existing_channel = (
                ctx.guild.get_channel(
                    int(existing_channel_id)
                )
            )

            if existing_channel:
                await ctx.reply(
                    (
                        f"{member.mention}, you already "
                        "have an active verification: "
                        f"{existing_channel.mention}"
                    ),
                    mention_author=False,
                )
                return

        # Stale database record.
        # If its old channel no longer exists,
        # allow a fresh verification channel.

    # ------------------------------------------
    # FIND VERIFICATION CATEGORY
    # ------------------------------------------

    category = ctx.guild.get_channel(
        VERIFICATION_CATEGORY_ID
    )

    if not isinstance(
        category,
        discord.CategoryChannel,
    ):
        await ctx.reply(
            (
                "A&T verification is not currently "
                "available because the verification "
                "category could not be found."
            ),
            mention_author=False,
        )

        print(
            "ONBOARDING ERROR: "
            "Verification category unavailable."
        )
        return

    # ------------------------------------------
    # PRIVATE CHANNEL PERMISSIONS
    # ------------------------------------------

    overwrites = {
        ctx.guild.default_role:
            discord.PermissionOverwrite(
                view_channel=False
            ),

        member:
            discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                embed_links=True,
                attach_files=True,
            ),

        ctx.guild.me:
            discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True,
                embed_links=True,
            ),
    }

    for role_id in (
        VERIFICATION_STAFF_ROLE_IDS
    ):
        role = ctx.guild.get_role(
            role_id
        )

        if role is not None:
            overwrites[role] = (
                discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_messages=True,
                    embed_links=True,
                )
            )

    # ------------------------------------------
    # CREATE PRIVATE CHANNEL
    # ------------------------------------------

    try:
        verification_channel = (
            await ctx.guild.create_text_channel(
                name=safe_channel_name(
                    member
                ),
                category=category,
                overwrites=overwrites,
                reason=(
                    "A&T Transport driver "
                    "verification started"
                ),
            )
        )

    except discord.Forbidden:
        await ctx.reply(
            (
                "I could not create your private "
                "verification channel. A&T "
                "Management has been notified."
            ),
            mention_author=False,
        )

        print(
            "ONBOARDING ERROR: "
            "Bot cannot create verification channels."
        )
        return

    except discord.HTTPException as error:
        await ctx.reply(
            (
                "Discord could not create your "
                "verification channel. Please try "
                "again shortly."
            ),
            mention_author=False,
        )

        print(
            "ONBOARDING CHANNEL ERROR: "
            f"{error}"
        )
        return

    # ------------------------------------------
    # SAVE APPLICATION
    # ------------------------------------------

    try:
        saved = await save_new_verification(
            member.id,
            verification_channel.id,
        )

    except Exception as error:
        print(
            "ONBOARDING DATABASE ERROR: "
            f"{error}"
        )

        try:
            await verification_channel.delete(
                reason=(
                    "A&T verification database "
                    "save failed"
                )
            )
        except discord.HTTPException:
            pass

        await ctx.reply(
            (
                "Your verification could not be "
                "started because of a database "
                "error. Please try again shortly."
            ),
            mention_author=False,
        )
        return

    if not saved:
        try:
            await verification_channel.delete(
                reason=(
                    "A&T verification database "
                    "unavailable"
                )
            )
        except discord.HTTPException:
            pass

        await ctx.reply(
            (
                "The A&T verification database "
                "is currently unavailable."
            ),
            mention_author=False,
        )
        return

    # ------------------------------------------
    # PRIVATE WELCOME MESSAGE
    # ------------------------------------------

    embed = discord.Embed(
        title=(
            "🚛 A&T TRANSPORT LTD — "
            "DRIVER VERIFICATION"
        ),
        description=(
            f"Welcome {member.mention}.\n\n"
            "This private channel has been created "
            "to link your Discord account with your "
            "TrucksBook driver profile.\n\n"
            "**Step 1 — Find your TrucksBook User ID**\n"
            "Open your TrucksBook profile. Your User "
            "ID is the number at the end of your "
            "profile URL.\n\n"
            "Example:\n"
            "`trucksbook.eu/profile/123456`\n\n"
            "The User ID in this example is "
            "`123456`.\n\n"
            "**Step 2 — Submit the ID**\n"
            "Type:\n"
            "`!trucksbook 123456`\n\n"
            "Replace `123456` with your own "
            "TrucksBook User ID.\n\n"
            "After submission, the account will "
            "move to the verification stage.\n\n"
            "If you started this by mistake, type:\n"
            "`!cancel`\n\n"
            "🔒 **This channel is private.** "
            "Only you and authorised A&T "
            "Management can see it."
        ),
        colour=discord.Colour.blue(),
    )

    embed.set_footer(
        text=(
            "A&T Transport LTD • "
            "Driven Beyond Horizons"
        )
    )

    await verification_channel.send(
        content=member.mention,
        embed=embed,
    )

    await ctx.reply(
        (
            f"{member.mention}, your private "
            "verification channel has been created: "
            f"{verification_channel.mention}"
        ),
        mention_author=False,
    )

    await log_verification(
        ctx.guild,
        "🔐 Driver Verification Started",
        (
            f"**Driver:** {member.mention}\n"
            f"**Discord ID:** `{member.id}`\n"
            f"**Channel:** "
            f"{verification_channel.mention}\n"
            "**Status:** Pending TrucksBook ID"
        ),
    )

    print("--------------------------------")
    print("A&T DRIVER VERIFICATION STARTED")
    print(
        f"Discord User: {member} "
        f"({member.id})"
    )
    print(
        "Verification Channel: "
        f"{verification_channel.id}"
    )
    print("--------------------------------")


@bot.command(
    name="trucksbook"
)
async def submit_trucksbook(
    ctx,
    trucksbook_user_id: str = None,
):
    if ctx.guild is None:
        return

    if ctx.guild.id != GUILD_ID:
        return

    # ------------------------------------------
    # FIND VERIFICATION
    # ------------------------------------------

    verification = (
        await get_verification_by_channel(
            ctx.channel.id
        )
    )

    if verification is None:
        await ctx.reply(
            (
                "This command can only be used "
                "inside your private A&T driver "
                "verification channel."
            ),
            mention_author=False,
        )
        return

    applicant_id = int(
        verification["discord_user_id"]
    )

    # ------------------------------------------
    # CHECK USER PERMISSION
    # ------------------------------------------

    if (
        ctx.author.id != applicant_id
        and not member_is_verification_staff(
            ctx.author
        )
    ):
        await ctx.reply(
            (
                "Only the applicant or authorised "
                "A&T Management can submit the "
                "TrucksBook ID for this application."
            ),
            mention_author=False,
        )
        return

    # ------------------------------------------
    # CHECK VERIFICATION STATUS
    # ------------------------------------------

    if verification["status"] != "pending":
        await ctx.reply(
            (
                "This verification is no longer "
                "waiting for a TrucksBook ID."
            ),
            mention_author=False,
        )
        return

    # ------------------------------------------
    # VALIDATE TRUCKSBOOK USER ID
    # ------------------------------------------

    if trucksbook_user_id is None:
        await ctx.reply(
            (
                "Please include your TrucksBook "
                "User ID.\n\n"
                "Example:\n"
                "`!trucksbook 123456`"
            ),
            mention_author=False,
        )
        return

    trucksbook_user_id = trucksbook_user_id.strip()

    if not trucksbook_user_id.isdigit():
        await ctx.reply(
            (
                "That does not look like a valid "
                "TrucksBook User ID.\n\n"
                "Please enter numbers only.\n"
                "Example: `!trucksbook 123456`"
            ),
            mention_author=False,
        )
        return

    numeric_id = int(trucksbook_user_id)

    if numeric_id <= 0:
        await ctx.reply(
            (
                "That does not look like a valid "
                "TrucksBook User ID."
            ),
            mention_author=False,
        )
        return

    # ------------------------------------------
    # CHECK TRUCKSBOOK PROFILE
    # ------------------------------------------

    checking_message = await ctx.reply(
        (
            "🔎 Checking TrucksBook profile "
            f"`{numeric_id}`...\n\n"
            "Please wait while A&T verifies "
            "the driver account."
        ),
        mention_author=False,
    )

    try:
        profile = await fetch_trucksbook_profile(
            numeric_id
        )

    except Exception as error:
        print(
            "TRUCKSBOOK PROFILE ERROR: "
            f"{error}"
        )

        await checking_message.edit(
            content=(
                "❌ An unexpected error occurred "
                "while checking TrucksBook.\n\n"
                "Please try again shortly."
            )
        )
        return

    # ------------------------------------------
    # PROFILE LOOKUP FAILED
    # ------------------------------------------

    if not profile.get("success"):
        reason = profile.get(
            "reason",
            "unknown_error",
        )

        if reason == "request_failed":
            message = (
                "❌ TrucksBook could not be reached "
                "at the moment.\n\n"
                "Please try again shortly."
            )

        elif reason == "http_error":
            status_code = profile.get(
                "status_code",
                "Unknown",
            )

            message = (
                "❌ TrucksBook returned an unexpected "
                "response.\n\n"
                f"HTTP Status: `{status_code}`\n\n"
                "Please try again shortly."
            )

        elif reason == "profile_not_found":
            message = (
                "❌ A TrucksBook profile could not "
                "be verified using that User ID.\n\n"
                "Please check the ID and try again."
            )

        else:
            message = (
                "❌ That TrucksBook profile could "
                "not be verified."
            )

        await checking_message.edit(
            content=message
        )

        await log_verification(
            ctx.guild,
            "❌ TrucksBook Profile Check Failed",
            (
                f"**Applicant:** <@{applicant_id}>\n"
                f"**Discord ID:** `{applicant_id}`\n"
                f"**Submitted TrucksBook ID:** "
                f"`{numeric_id}`\n"
                f"**Channel:** "
                f"{ctx.channel.mention}\n"
                f"**Reason:** `{reason}`"
            ),
        )
        return

    # ------------------------------------------
    # EXTRACT VERIFIED PROFILE
    # ------------------------------------------

    profile_name = profile.get(
        "trucksbook_name"
    )

    company_name = profile.get(
        "company_name"
    )

    company_position = profile.get(
        "company_position"
    )

    belongs_to_at = profile.get(
        "belongs_to_at",
        False,
    )

    # ------------------------------------------
    # REQUIRE A&T MEMBERSHIP
    # ------------------------------------------

    if not belongs_to_at:
        await checking_message.edit(
            content=(
                "❌ **A&T membership could not be "
                "verified.**\n\n"
                "The TrucksBook profile was found, "
                "but it is not currently listed with "
                "**A & T Transport LTD**.\n\n"
                "If you believe this is incorrect, "
                "please contact A&T Management."
            )
        )

        await log_verification(
            ctx.guild,
            "❌ A&T Membership Verification Failed",
            (
                f"**Applicant:** <@{applicant_id}>\n"
                f"**Discord ID:** `{applicant_id}`\n"
                f"**TrucksBook ID:** `{numeric_id}`\n"
                f"**Detected Driver:** "
                f"`{profile_name or 'Unknown'}`\n"
                f"**Detected Company:** "
                f"`{company_name or 'None'}`\n"
                f"**Channel:** "
                f"{ctx.channel.mention}"
            ),
        )
        return

    # ------------------------------------------
    # SAVE VERIFIED TRUCKSBOOK ID
    # ------------------------------------------

    try:
        result = await save_trucksbook_id(
            applicant_id,
            numeric_id,
        )

    except Exception as error:
        print(
            "TRUCKSBOOK ID DATABASE ERROR: "
            f"{error}"
        )

        await checking_message.edit(
            content=(
                "❌ There was a database error while "
                "saving the verified TrucksBook "
                "account.\n\n"
                "Please try again shortly."
            )
        )
        return

    # ------------------------------------------
    # DUPLICATE LINK PROTECTION
    # ------------------------------------------

    if result == "already_linked":
        await checking_message.edit(
            content=(
                "❌ That TrucksBook User ID is "
                "already linked to another A&T "
                "Discord account.\n\n"
                "Please contact A&T Management "
                "if you believe this is incorrect."
            )
        )

        await log_verification(
            ctx.guild,
            "⚠️ Duplicate TrucksBook ID Attempt",
            (
                f"**Applicant:** <@{applicant_id}>\n"
                f"**Discord ID:** `{applicant_id}`\n"
                f"**TrucksBook ID:** `{numeric_id}`\n"
                f"**Channel:** "
                f"{ctx.channel.mention}\n"
                "**Result:** Already linked"
            ),
        )
        return

    if result == "already_pending":
        await checking_message.edit(
            content=(
                "❌ That TrucksBook User ID is "
                "already being used in another "
                "active verification.\n\n"
                "Please contact A&T Management "
                "if you believe this is incorrect."
            )
        )

        await log_verification(
            ctx.guild,
            "⚠️ Duplicate Verification Attempt",
            (
                f"**Applicant:** <@{applicant_id}>\n"
                f"**Discord ID:** `{applicant_id}`\n"
                f"**TrucksBook ID:** `{numeric_id}`\n"
                f"**Channel:** "
                f"{ctx.channel.mention}\n"
                "**Result:** Already pending"
            ),
        )
        return

    if result is not True:
        await checking_message.edit(
            content=(
                "❌ The verified TrucksBook ID "
                "could not be saved.\n\n"
                "Please try again."
            )
        )
        return

    # ------------------------------------------
    # SAVE VERIFIED PROFILE NAME + STATUS
    # ------------------------------------------

    try:
        async with db_pool.acquire() as connection:
            update_result = await connection.execute(
                """
                UPDATE driver_verifications
                SET
                    trucksbook_name = $1,
                    status = 'confirmed',
                    confirmed_at = NOW()
                WHERE discord_user_id = $2
                  AND trucksbook_user_id = $3
                  AND status = 'pending';
                """,
                profile_name,
                applicant_id,
                numeric_id,
            )

    except Exception as error:
        print(
            "TRUCKSBOOK VERIFICATION DATABASE ERROR: "
            f"{error}"
        )

        await checking_message.edit(
            content=(
                "❌ The TrucksBook profile was "
                "verified, but its verification "
                "record could not be updated.\n\n"
                "Please contact A&T Management."
            )
        )
        return

    if update_result == "UPDATE 0":
        await checking_message.edit(
            content=(
                "❌ The verification record changed "
                "before the profile could be saved.\n\n"
                "Please contact A&T Management."
            )
        )
        return

    # ------------------------------------------
    # REMOVE CHECKING MESSAGE
    # ------------------------------------------

    try:
        await checking_message.delete()
    except discord.HTTPException:
        pass

    # ------------------------------------------
    # SHOW DRIVER CONFIRMATION
    # ------------------------------------------

    applicant = ctx.guild.get_member(
        applicant_id
    )

    applicant_text = (
        applicant.mention
        if applicant
        else f"<@{applicant_id}>"
    )

    embed = discord.Embed(
        title="✅ TRUCKSBOOK PROFILE VERIFIED",
        description=(
            f"{applicant_text}\n\n"
            "Your TrucksBook profile has been "
            "successfully verified with "
            "**A & T Transport LTD**.\n\n"
            "Please check the information below "
            "before continuing."
        ),
        colour=discord.Colour.green(),
    )

    embed.add_field(
        name="TrucksBook User ID",
        value=f"`{numeric_id}`",
        inline=True,
    )

    embed.add_field(
        name="Driver",
        value=f"`{profile_name or 'Unknown'}`",
        inline=True,
    )

    embed.add_field(
        name="A&T Member",
        value="✅ Yes",
        inline=True,
    )

    embed.add_field(
        name="Company",
        value=f"`{company_name or 'Unknown'}`",
        inline=True,
    )

    embed.add_field(
        name="Company Position",
        value=f"`{company_position or 'Not listed'}`",
        inline=True,
    )

    embed.add_field(
        name="Verification",
        value="✅ Automatically verified",
        inline=True,
    )

    embed.add_field(
        name="Final Confirmation",
        value=(
            "If these details are correct, type:\n"
            "**`!confirm`**\n\n"
            "Your Discord account will **not** be "
            "permanently linked until you confirm."
        ),
        inline=False,
    )

    embed.set_footer(
        text=(
            "A&T Transport LTD • "
            "Driven Beyond Horizons"
        )
    )

    await ctx.send(
        embed=embed
    )

    # ------------------------------------------
    # PERMANENT MANAGEMENT LOG
    # ------------------------------------------

    await log_verification(
        ctx.guild,
        "✅ TrucksBook Profile Verified",
        (
            f"**Applicant:** {applicant_text}\n"
            f"**Discord ID:** `{applicant_id}`\n"
            f"**TrucksBook ID:** `{numeric_id}`\n"
            f"**Driver:** "
            f"`{profile_name or 'Unknown'}`\n"
            f"**Company:** "
            f"`{company_name or 'Unknown'}`\n"
            f"**Position:** "
            f"`{company_position or 'Not listed'}`\n"
            f"**Channel:** {ctx.channel.mention}\n"
            "**Status:** Awaiting driver confirmation"
        ),
    )

    print("--------------------------------")
    print("TRUCKSBOOK PROFILE VERIFIED")
    print(f"Discord User ID: {applicant_id}")
    print(f"TrucksBook User ID: {numeric_id}")
    print(f"Driver: {profile_name}")
    print(f"Company: {company_name}")
    print(f"Position: {company_position}")
    print("STATUS: Awaiting !confirm")
    print("--------------------------------")

@bot.command(
    name="confirm"
)
async def confirm_verification(
    ctx,
):
    if ctx.guild is None:
        return

    if ctx.guild.id != GUILD_ID:
        return

    if db_pool is None:
        await ctx.reply(
            (
                "❌ The A&T verification database is "
                "currently unavailable.\n\n"
                "Please try again shortly."
            ),
            mention_author=False,
        )
        return

    # ------------------------------------------
    # FIND VERIFICATION
    # ------------------------------------------

    verification = (
        await get_verification_by_channel(
            ctx.channel.id
        )
    )

    if verification is None:
        await ctx.reply(
            (
                "This command can only be used "
                "inside an active A&T driver "
                "verification channel."
            ),
            mention_author=False,
        )
        return

    applicant_id = int(
        verification["discord_user_id"]
    )

    # ------------------------------------------
    # APPLICANT MUST CONFIRM THEMSELVES
    # ------------------------------------------

    if ctx.author.id != applicant_id:
        await ctx.reply(
            (
                "Only the applicant can complete "
                "their own driver verification."
            ),
            mention_author=False,
        )
        return

    # ------------------------------------------
    # REQUIRE VERIFIED PROFILE
    # ------------------------------------------

    if verification["status"] != "confirmed":
        await ctx.reply(
            (
                "Your TrucksBook profile has not "
                "been verified yet.\n\n"
                "Please submit your TrucksBook "
                "User ID first using:\n"
                "`!trucksbook YOUR_ID`"
            ),
            mention_author=False,
        )
        return

    trucksbook_name = verification[
        "trucksbook_name"
    ]

    trucksbook_user_id = verification[
        "trucksbook_user_id"
    ]

    if (
        not trucksbook_name
        or trucksbook_user_id is None
    ):
        await ctx.reply(
            (
                "❌ Your verification record is "
                "missing TrucksBook information.\n\n"
                "Please contact A&T Management."
            ),
            mention_author=False,
        )
        return

    trucksbook_user_id = int(
        trucksbook_user_id
    )

    # ------------------------------------------
    # NORMALISE DRIVER NAME
    # ------------------------------------------
    #
    # TrucksBook profiles can display:
    # "A&T Mystical Custom"
    #
    # while the job-delivery webhook can use:
    # "Mystical Custom".
    #
    # Store the webhook-compatible name so
    # automatic mileage matching keeps working.
    # ------------------------------------------

    link_name = normalise_trucksbook_name(
        trucksbook_name
    )

    if not link_name:
        await ctx.reply(
            "❌ The verified TrucksBook driver name is invalid.",
            mention_author=False,
        )
        return

    # ------------------------------------------
    # CREATE PERMANENT DRIVER LINK
    # ------------------------------------------

    final_real_miles = 0

    try:
        async with db_pool.acquire() as connection:
            async with connection.transaction():

                # Lock and re-check the verification inside the same
                # transaction. This makes repeated/concurrent !confirm
                # attempts safe and prevents stale verification data
                # from being committed.
                locked_verification = await connection.fetchrow(
                    """
                    SELECT
                        status,
                        trucksbook_name,
                        trucksbook_user_id
                    FROM driver_verifications
                    WHERE discord_user_id = $1
                      AND verification_channel_id = $2
                    FOR UPDATE;
                    """,
                    applicant_id,
                    ctx.channel.id,
                )

                if (
                    locked_verification is None
                    or locked_verification["status"] != "confirmed"
                    or locked_verification["trucksbook_name"]
                        != trucksbook_name
                    or int(
                        locked_verification["trucksbook_user_id"]
                    ) != trucksbook_user_id
                ):
                    raise RuntimeError(
                        "Verification record changed before completion."
                    )

                # ----------------------------------
                # CHECK DISCORD ACCOUNT
                # ----------------------------------

                existing_discord = (
                    await connection.fetchrow(
                        """
                        SELECT
                            trucksbook_name,
                            trucksbook_user_id
                        FROM driver_links
                        WHERE discord_user_id = $1;
                        """,
                        applicant_id,
                    )
                )

                if existing_discord is not None:
                    await ctx.reply(
                        (
                            "❌ Your Discord account "
                            "is already linked to an "
                            "A&T TrucksBook profile.\n\n"
                            "Please contact A&T "
                            "Management if you believe "
                            "this is incorrect."
                        ),
                        mention_author=False,
                    )
                    return

                # ----------------------------------
                # CHECK TRUCKSBOOK USER ID
                # ----------------------------------

                existing_tb_id = (
                    await connection.fetchval(
                        """
                        SELECT discord_user_id
                        FROM driver_links
                        WHERE trucksbook_user_id = $1;
                        """,
                        trucksbook_user_id,
                    )
                )

                if existing_tb_id is not None:
                    await ctx.reply(
                        (
                            "❌ This TrucksBook account "
                            "is already linked to "
                            "another Discord account.\n\n"
                            "Please contact A&T "
                            "Management."
                        ),
                        mention_author=False,
                    )
                    return

                # ----------------------------------
                # CHECK DRIVER NAME
                # ----------------------------------

                existing_name = (
                    await connection.fetchval(
                        """
                        SELECT discord_user_id
                        FROM driver_links
                        WHERE LOWER(
                            REGEXP_REPLACE(
                                BTRIM(trucksbook_name),
                                '^a\\s*&\\s*t(\\s+|$)',
                                '',
                                'i'
                            )
                        ) = LOWER($1);
                        """,
                        link_name,
                    )
                )

                if existing_name is not None:
                    await ctx.reply(
                        (
                            "❌ This TrucksBook driver "
                            "is already linked to "
                            "another Discord account.\n\n"
                            "Please contact A&T "
                            "Management."
                        ),
                        mention_author=False,
                    )
                    return

                # ----------------------------------
                # INSERT PERMANENT LINK
                # ----------------------------------

                await connection.execute(
                    """
                    INSERT INTO driver_links (
                        trucksbook_name,
                        discord_user_id,
                        trucksbook_user_id,
                        linked_at
                    )
                    VALUES (
                        $1,
                        $2,
                        $3,
                        NOW()
                    );
                    """,
                    link_name,
                    applicant_id,
                    trucksbook_user_id,
                )

                # ----------------------------------
                # INITIALISE DRIVER PROGRESSION
                # ----------------------------------

                await connection.execute(
                    """
                    INSERT INTO driver_progress (
                        discord_user_id,
                        trucksbook_name,
                        real_miles,
                        updated_at
                    )
                    VALUES (
                        $1,
                        $2,
                        0,
                        NOW()
                    )
                    ON CONFLICT (discord_user_id)
                    DO NOTHING;
                    """,
                    applicant_id,
                    link_name,
                )

                # Preserve any legitimate pre-existing mileage record.
                # A normal first-time driver receives zero miles.
                final_real_miles = int(
                    await connection.fetchval(
                        """
                        SELECT real_miles
                        FROM driver_progress
                        WHERE discord_user_id = $1;
                        """,
                        applicant_id,
                    )
                    or 0
                )

                # ----------------------------------
                # COMPLETE VERIFICATION
                # ----------------------------------

                update_result = (
                    await connection.execute(
                        """
                        UPDATE driver_verifications
                        SET
                            status = 'completed',
                            completed_at = NOW()
                        WHERE discord_user_id = $1
                          AND verification_channel_id = $2
                          AND status = 'confirmed';
                        """,
                        applicant_id,
                        ctx.channel.id,
                    )
                )

                if update_result == "UPDATE 0":
                    raise RuntimeError(
                        "Verification record changed "
                        "before completion."
                    )

    except asyncpg.UniqueViolationError:
        await ctx.reply(
            (
                "❌ This Discord or TrucksBook "
                "account has already been linked.\n\n"
                "Please contact A&T Management."
            ),
            mention_author=False,
        )
        return

    except Exception as error:
        print(
            "VERIFICATION CONFIRM ERROR: "
            f"{error}"
        )

        await ctx.reply(
            (
                "❌ Your verification could not be "
                "completed because of a database "
                "error.\n\n"
                "No duplicate link will be created. "
                "Please contact A&T Management."
            ),
            mention_author=False,
        )
        return

    # ------------------------------------------
    # ASSIGN NEW DRIVER PROGRESSION ROLE
    # ------------------------------------------

    role_result = None

    try:
        role_result = (
            await sync_member_progression_role(
                ctx.guild,
                applicant_id,
                final_real_miles,
            )
        )

    except discord.Forbidden:
        print(
            "CONFIRM ROLE ERROR: "
            "Bot cannot manage applicant roles."
        )

    except discord.HTTPException as error:
        print(
            "CONFIRM ROLE ERROR: "
            f"{error}"
        )

    # ------------------------------------------
    # SUCCESS MESSAGE
    # ------------------------------------------

    embed = discord.Embed(
        title="🎉 A&T DRIVER VERIFICATION COMPLETE",
        description=(
            f"{ctx.author.mention}\n\n"
            "Your Discord account has now been "
            "successfully linked to your verified "
            "A&T TrucksBook driver profile.\n\n"
            "Welcome to **A&T Transport LTD**."
        ),
        colour=discord.Colour.green(),
    )

    embed.add_field(
        name="TrucksBook Driver",
        value=f"`{trucksbook_name}`",
        inline=True,
    )

    embed.add_field(
        name="TrucksBook User ID",
        value=f"`{trucksbook_user_id}`",
        inline=True,
    )

    embed.add_field(
        name="Mileage",
        value=f"`{final_real_miles:,} Real miles`",
        inline=True,
    )

    embed.add_field(
        name="Progression",
        value="🚛 **A&T New Driver**",
        inline=True,
    )

    embed.add_field(
        name="Mileage Tracking",
        value=(
            "Your qualifying **Real** TrucksBook "
            "jobs can now be added automatically "
            "to your A&T progression."
        ),
        inline=False,
    )

    embed.set_footer(
        text=(
            "A&T Transport LTD • "
            "Driven Beyond Horizons"
        )
    )

    await ctx.send(
        embed=embed
    )

    # ------------------------------------------
    # PERMANENT MANAGEMENT LOG
    # ------------------------------------------

    role_status = "Unknown"

    if role_result:
        role_status = (
            role_result.get("role_name")
            or role_result.get("status")
            or "Unknown"
        )

    await log_verification(
        ctx.guild,
        "🎉 Driver Verification Completed",
        (
            f"**Driver:** {ctx.author.mention}\n"
            f"**Discord ID:** `{applicant_id}`\n"
            f"**TrucksBook Profile:** "
            f"`{trucksbook_name}`\n"
            f"**Webhook Driver Name:** "
            f"`{link_name}`\n"
            f"**TrucksBook ID:** "
            f"`{trucksbook_user_id}`\n"
            f"**Starting Mileage:** "
            f"`{final_real_miles:,} Real miles`\n"
            f"**Progression Role:** "
            f"`{role_status}`\n"
            "**Status:** Completed"
        ),
    )

    print("--------------------------------")
    print("A&T DRIVER VERIFICATION COMPLETE")
    print(f"Discord User ID: {applicant_id}")
    print(
        f"TrucksBook Profile: "
        f"{trucksbook_name}"
    )
    print(
        f"Webhook Driver Name: "
        f"{link_name}"
    )
    print(
        f"TrucksBook User ID: "
        f"{trucksbook_user_id}"
    )
    print(f"Starting Mileage: {final_real_miles}")
    print("--------------------------------")

    # ------------------------------------------
    # CLOSE TEMPORARY CHANNEL
    # ------------------------------------------

    await ctx.send(
        (
            "🔒 Verification complete.\n"
            "This private channel will close "
            "automatically in **30 seconds**."
        )
    )

    await discord.utils.sleep_until(
        discord.utils.utcnow()
        + __import__("datetime").timedelta(
            seconds=30
        )
    )

    try:
        await ctx.channel.delete(
            reason=(
                "A&T driver verification completed"
            )
        )

    except discord.Forbidden:
        print(
            "VERIFICATION CHANNEL DELETE ERROR: "
            "Missing Manage Channels permission."
        )

    except discord.HTTPException as error:
        print(
            "VERIFICATION CHANNEL DELETE ERROR: "
            f"{error}"
        )

@bot.command(
    name="cancel"
)
async def cancel_verification(
    ctx,
):
    if ctx.guild is None:
        return

    if ctx.guild.id != GUILD_ID:
        return

    verification = (
        await get_verification_by_channel(
            ctx.channel.id
        )
    )

    if verification is None:
        return

    applicant_id = int(
        verification["discord_user_id"]
    )

    if (
        ctx.author.id != applicant_id
        and not member_is_verification_staff(
            ctx.author
        )
    ):
        await ctx.reply(
            (
                "Only the applicant or authorised "
                "A&T Management can cancel this "
                "verification."
            ),
            mention_author=False,
        )
        return

    applicant = ctx.guild.get_member(
        applicant_id
    )

    applicant_text = (
        applicant.mention
        if applicant
        else f"<@{applicant_id}>"
    )

    await cancel_verification_record(
        applicant_id
    )

    await log_verification(
        ctx.guild,
        "❌ Driver Verification Cancelled",
        (
            f"**Driver:** {applicant_text}\n"
            f"**Discord ID:** `{applicant_id}`\n"
            f"**Cancelled By:** "
            f"{ctx.author.mention}\n"
            f"**Channel:** `#{ctx.channel.name}`"
        ),
    )

    embed = discord.Embed(
        title=(
            "❌ VERIFICATION CANCELLED"
        ),
        description=(
            "This A&T driver verification has "
            "been cancelled.\n\n"
            "This private channel will now be "
            "removed."
        ),
        colour=discord.Colour.red(),
    )

    await ctx.send(
        embed=embed
    )

    print("--------------------------------")
    print("A&T DRIVER VERIFICATION CANCELLED")
    print(
        f"Discord User ID: {applicant_id}"
    )
    print(
        f"Cancelled By: {ctx.author}"
    )
    print("--------------------------------")

    # Give Discord a moment to display the
    # cancellation message before deletion.
    import asyncio

    await asyncio.sleep(5)

    try:
        await ctx.channel.delete(
            reason=(
                "A&T driver verification cancelled"
            )
        )
    except discord.Forbidden:
        print(
            "ONBOARDING ERROR: "
            "Bot cannot delete verification channel."
        )
    except discord.HTTPException as error:
        print(
            "ONBOARDING DELETE ERROR: "
            f"{error}"
        )

# --------------------------------------------------
# ONBOARDING COMMAND ERROR HANDLING
# --------------------------------------------------

@verify_driver.error
async def verify_driver_error(
    ctx,
    error,
):
    print(
        "VERIFY COMMAND ERROR: "
        f"{error}"
    )


@submit_trucksbook.error
async def submit_trucksbook_error(
    ctx,
    error,
):
    print(
        "TRUCKSBOOK COMMAND ERROR: "
        f"{error}"
    )

    await ctx.reply(
        (
            "I couldn't process that TrucksBook "
            "ID. Please use:\n"
            "`!trucksbook 123456`"
        ),
        mention_author=False,
    )


@cancel_verification.error
async def cancel_verification_error(
    ctx,
    error,
):
    print(
        "CANCEL COMMAND ERROR: "
        f"{error}"
    )

# --------------------------------------------------
# START BOT
# --------------------------------------------------

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN has not been configured."
    )

bot.run(TOKEN)
