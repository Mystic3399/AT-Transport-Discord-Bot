import os
import re
import asyncio
import math
import unicodedata
from io import BytesIO
from pathlib import Path

import aiohttp
import asyncpg
import discord
from bs4 import BeautifulSoup
from discord.ext import commands

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
except ImportError:
    Image = None
    ImageDraw = None
    ImageFilter = None
    ImageFont = None
    ImageOps = None


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
    (
        75000,
        int(os.getenv("ROLE_HORIZON_WOLF")),
        "Horizon Wolf",
    ),
    (
        100000,
        int(os.getenv("ROLE_AT_CENTURION")),
        "A&T Centurion",
    ),
    (
        150000,
        int(os.getenv("ROLE_VIKING_PATHFINDER")),
        "Viking Pathfinder",
    ),
    (
        200000,
        int(os.getenv("ROLE_ROAD_GUARDIAN")),
        "Road Guardian",
    ),
    (
        300000,
        int(os.getenv("ROLE_AURORA_PATHFINDER")),
        "Aurora Pathfinder",
    ),
    (
        400000,
        int(os.getenv("ROLE_VIKING_ROADMASTER")),
        "Viking Roadmaster",
    ),
    (
        500000,
        int(os.getenv("ROLE_HORIZON_LEGEND")),
        "Horizon Legend",
    ),
    (
        600000,
        int(os.getenv("ROLE_ALPHA_ROADMASTER")),
        "Alpha Roadmaster",
    ),
    (
        700000,
        int(os.getenv("ROLE_AURORA_COMMANDER")),
        "Aurora Commander",
    ),
    (
        800000,
        int(os.getenv("ROLE_GUARDIAN_HORIZON")),
        "Guardian of the Horizon",
    ),
    (
        900000,
        int(os.getenv("ROLE_BEYOND_HORIZON")),
        "Beyond the Horizon",
    ),
    (
        1000000,
        int(os.getenv("ROLE_AT_IMMORTAL")),
        "A&T Immortal",
    ),
]

PROGRESSION_BADGES = {
    0: "at-new-driver-0.png",
    5000: "road-runner-5000.png",
    10000: "distance-driver-10000.png",
    15000: "aurora-driver-15000.png",
    20000: "wolf-pack-driver-20000.png",
    25000: "viking-hauler-25000.png",
    30000: "elite-hauler-30000.png",
    35000: "at-veteran-35000.png",
    40000: "at-road-legend-40000.png",
    50000: "beyond-horizons-50000.png",
    75000: "horizon-wolf-75000.png",
    100000: "at-centurion-100000.png",
    150000: "viking-pathfinder-150000.png",
    200000: "road-guardian-200000.png",
    300000: "aurora-pathfinder-300000.png",
    400000: "viking-roadmaster-400000.png",
    500000: "horizon-legend-500000.png",
    600000: "alpha-roadmaster-600000.png",
    700000: "aurora-commander-700000.png",
    800000: "guardian-of-the-horizon-800000.png",
    900000: "beyond-the-horizon-900000.png",
    1000000: "at-immortal-1000000.png",
}

BADGE_DIRECTORY = Path(__file__).resolve().parent / "assets" / "badges"


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

        # Staged corrections discovered by the read-only history audit.
        # Merely populating this table never changes driver mileage.
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS mileage_repair_candidates (
                job_id TEXT PRIMARY KEY,
                discord_user_id BIGINT NOT NULL,
                trucksbook_name TEXT NOT NULL,
                recorded_distance INTEGER NOT NULL,
                actual_distance INTEGER NOT NULL,
                missing_distance INTEGER NOT NULL CHECK (missing_distance > 0),
                source_channel_id BIGINT NOT NULL,
                source_message_id BIGINT NOT NULL,
                audited_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                applied_at TIMESTAMPTZ,
                applied_by BIGINT
            );
            """
        )

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS repair_achievement_announcements (
                discord_user_id BIGINT NOT NULL,
                milestone_miles BIGINT NOT NULL,
                announced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (discord_user_id, milestone_miles)
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
    """Parse a non-negative integer with common thousands separators.

    TrucksBook has emitted distances as ``3813``, ``3,813`` and
    ``3 813`` (including non-breaking spaces).  The previous expression
    stopped at whitespace, turning ``3 813`` into ``3``.
    """
    if not value:
        return None

    match = re.search(
        r"\d+(?:(?:\s+|,\s*)\d+)*",
        str(value),
    )

    if not match:
        return None

    try:
        return int(
            re.sub(r"[\s,]", "", match.group(0))
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
        return False

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
            return False

    if guild is not None and getattr(channel, "guild", None) != guild:
        print(
            "MILESTONE ANNOUNCEMENT SKIPPED: Configured channel is "
            "not in the A&T Transport LTD server."
        )
        return False

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
        return False

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
    return True


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
# MILEAGE AUDIT / REPAIR (ADMINISTRATOR ONLY)
# --------------------------------------------------

def extract_trucksbook_job_from_embed(embed):
    """Return audit fields from a TrucksBook delivery embed, if present."""
    data = embed.to_dict()
    combined_text = (
        f"{data.get('title') or ''}\n"
        f"{data.get('description') or ''}"
    )
    job_match = re.search(
        r"Job delivery #(\d+)",
        combined_text,
        re.IGNORECASE,
    )
    if not job_match:
        return None

    details = ""
    for field in data.get("fields") or []:
        if (field.get("name") or "").strip().lower() == "details":
            details = field.get("value") or ""
            break

    statistics = None
    accepted_distance = None
    for line in details.splitlines():
        clean_line = line.replace("**", "").strip()
        lower_line = clean_line.lower()
        if lower_line.startswith("statistics:"):
            statistics = clean_line.split(":", 1)[1].strip()
        elif lower_line.startswith("accepted distance:"):
            accepted_distance = extract_integer(
                clean_line.split(":", 1)[1].strip()
            )

    return {
        "job_id": job_match.group(1),
        "statistics": statistics,
        "accepted_distance": accepted_distance,
    }


@bot.command(name="mileageaudit")
@commands.guild_only()
@commands.has_guild_permissions(administrator=True)
async def mileage_audit(ctx, message_limit: int = 0):
    """Dry-run audit of TrucksBook embeds across readable guild channels."""
    if ctx.guild.id != GUILD_ID:
        return False
    if db_pool is None:
        await ctx.reply("❌ The database is unavailable.", mention_author=False)
        return
    if message_limit < 0 or message_limit > 100000:
        await ctx.reply(
            "Use `!mileageaudit` for all history or a limit up to 100000.",
            mention_author=False,
        )
        return

    await ctx.reply(
        "🔎 Starting a server-wide dry-run audit. No mileage will be changed.",
        mention_author=False,
    )

    scanned = 0
    matched = 0
    staged = 0
    missing_from_database = 0
    channels_scanned = 0
    channels_skipped = 0
    history_limit = message_limit or None

    for channel in ctx.guild.text_channels:
        bot_member = ctx.guild.me
        permissions = channel.permissions_for(bot_member)
        if not (
            permissions.view_channel
            and permissions.read_message_history
        ):
            channels_skipped += 1
            print(
                "MILEAGE AUDIT SKIPPED CHANNEL: "
                f"#{channel.name} ({channel.id}) — missing permissions."
            )
            continue

        try:
            async for historical_message in channel.history(
                limit=history_limit,
                oldest_first=True,
            ):
                if historical_message.webhook_id is None:
                    continue
                for embed in historical_message.embeds:
                    parsed = extract_trucksbook_job_from_embed(embed)
                    if parsed is None:
                        continue
                    scanned += 1
                    if (
                        (parsed["statistics"] or "").casefold() != "real"
                        or parsed["accepted_distance"] is None
                    ):
                        continue

                    async with db_pool.acquire() as connection:
                        recorded = await connection.fetchrow(
                            """
                            SELECT
                                discord_user_id,
                                trucksbook_name,
                                accepted_distance
                            FROM processed_jobs
                            WHERE job_id = $1
                              AND LOWER(statistics) = 'real';
                            """,
                            parsed["job_id"],
                        )
                        if recorded is None:
                            missing_from_database += 1
                            continue

                        matched += 1
                        actual = int(parsed["accepted_distance"])
                        credited = int(recorded["accepted_distance"])
                        if actual <= credited:
                            continue

                        await connection.execute(
                            """
                            INSERT INTO mileage_repair_candidates (
                                job_id,
                                discord_user_id,
                                trucksbook_name,
                                recorded_distance,
                                actual_distance,
                                missing_distance,
                                source_channel_id,
                                source_message_id,
                                audited_at
                            )
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
                            ON CONFLICT (job_id) DO UPDATE SET
                                discord_user_id = EXCLUDED.discord_user_id,
                                trucksbook_name = EXCLUDED.trucksbook_name,
                                recorded_distance = EXCLUDED.recorded_distance,
                                actual_distance = EXCLUDED.actual_distance,
                                missing_distance = EXCLUDED.missing_distance,
                                source_channel_id = EXCLUDED.source_channel_id,
                                source_message_id = EXCLUDED.source_message_id,
                                audited_at = NOW()
                            WHERE mileage_repair_candidates.applied_at IS NULL;
                            """,
                            parsed["job_id"],
                            int(recorded["discord_user_id"]),
                            recorded["trucksbook_name"],
                            credited,
                            actual,
                            actual - credited,
                            channel.id,
                            historical_message.id,
                        )
                        staged += 1

            channels_scanned += 1

        except (discord.Forbidden, discord.HTTPException) as error:
            channels_skipped += 1
            print(
                "MILEAGE AUDIT SKIPPED CHANNEL: "
                f"#{channel.name} ({channel.id}) — {error}"
            )
            continue

    async with db_pool.acquire() as connection:
        totals = await connection.fetchrow(
            """
            SELECT
                COUNT(*) AS jobs,
                COUNT(DISTINCT discord_user_id) AS drivers,
                COALESCE(SUM(missing_distance), 0) AS missing_miles
            FROM mileage_repair_candidates
            WHERE applied_at IS NULL;
            """
        )

    await ctx.send(
        "✅ **Dry-run audit complete — no mileage changed.**\n"
        f"Server channels scanned: `{channels_scanned:,}`\n"
        f"Channels skipped (permissions/API): `{channels_skipped:,}`\n"
        f"Webhook jobs inspected: `{scanned:,}`\n"
        f"Previously processed Real jobs matched: `{matched:,}`\n"
        f"Candidates found/updated this run: `{staged:,}`\n"
        f"Webhook jobs absent from processed_jobs: `{missing_from_database:,}`\n"
        f"Pending corrections: `{int(totals['jobs']):,}` jobs across "
        f"`{int(totals['drivers']):,}` drivers\n"
        f"Missing mileage pending: `{int(totals['missing_miles']):,}`\n\n"
        "Review the Railway log/database backup, then explicitly run "
        "`!mileageapply CONFIRM` to apply these deltas."
    )


async def announce_pending_repair_achievements(guild):
    """Announce repair-created milestones once, including older repairs."""
    async with db_pool.acquire() as connection:
        pending = await connection.fetch(
            """
            SELECT
                milestones.discord_user_id,
                milestones.milestone_miles,
                milestones.trucksbook_name,
                COALESCE(progress.real_miles, 0) AS real_miles
            FROM announced_milestones AS milestones
            INNER JOIN mileage_repair_candidates AS repairs
                ON repairs.job_id = milestones.job_id
               AND repairs.applied_at IS NOT NULL
            LEFT JOIN repair_achievement_announcements AS sent
                ON sent.discord_user_id = milestones.discord_user_id
               AND sent.milestone_miles = milestones.milestone_miles
            LEFT JOIN driver_progress AS progress
                ON progress.discord_user_id = milestones.discord_user_id
            WHERE sent.discord_user_id IS NULL
            ORDER BY milestones.discord_user_id, milestones.milestone_miles;
            """
        )

    sent_count = 0
    failed_count = 0
    for row in pending:
        milestone_miles = int(row["milestone_miles"])
        rank_name = next(
            (
                role_name
                for required_miles, _, role_name in PROGRESSION_ROLES
                if required_miles == milestone_miles
            ),
            None,
        )
        if rank_name is None:
            failed_count += 1
            continue

        user_id = int(row["discord_user_id"])
        member = guild.get_member(user_id) if guild is not None else None
        sent = await announce_progression_milestone(
            guild=guild,
            member=member,
            discord_user_id=user_id,
            trucksbook_name=row["trucksbook_name"],
            milestone={"miles": milestone_miles, "rank": rank_name},
            real_miles=int(row["real_miles"]),
        )
        if not sent:
            failed_count += 1
            continue

        async with db_pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO repair_achievement_announcements (
                    discord_user_id, milestone_miles
                )
                VALUES ($1, $2)
                ON CONFLICT (discord_user_id, milestone_miles)
                DO NOTHING;
                """,
                user_id,
                milestone_miles,
            )
        sent_count += 1

    return sent_count, failed_count


@bot.command(name="repairachievements")
@commands.guild_only()
@commands.has_guild_permissions(administrator=True)
@commands.max_concurrency(1, per=commands.BucketType.guild, wait=False)
async def repair_achievements(ctx):
    """Post any milestone achievements created by the mileage repair."""
    if ctx.guild.id != GUILD_ID:
        return
    if db_pool is None:
        await ctx.reply("❌ The database is unavailable.", mention_author=False)
        return

    await ctx.reply(
        "🏆 Checking repaired mileage for unposted achievements...",
        mention_author=False,
    )
    sent_count, failed_count = await announce_pending_repair_achievements(
        ctx.guild
    )
    await ctx.send(
        "✅ **Repair achievement check complete.**\n"
        f"Achievements posted: `{sent_count:,}`\n"
        f"Could not post: `{failed_count:,}`\n"
        "Running this command again will not repost completed achievements."
    )


@bot.command(name="mileageapply")
@commands.guild_only()
@commands.has_guild_permissions(administrator=True)
async def mileage_apply(ctx, confirmation: str = ""):
    """Apply staged positive deltas once and announce earned milestones."""
    if ctx.guild.id != GUILD_ID:
        return
    if confirmation != "CONFIRM":
        await ctx.reply(
            "No changes made. After reviewing the dry run and taking a "
            "database backup, use `!mileageapply CONFIRM`.",
            mention_author=False,
        )
        return
    if db_pool is None:
        await ctx.reply("❌ The database is unavailable.", mention_author=False)
        return

    affected_users = set()
    applied_jobs = 0
    applied_miles = 0

    async with db_pool.acquire() as connection:
        async with connection.transaction():
            candidates = await connection.fetch(
                """
                SELECT *
                FROM mileage_repair_candidates
                WHERE applied_at IS NULL
                ORDER BY discord_user_id, job_id
                FOR UPDATE;
                """
            )

            for candidate in candidates:
                job = await connection.fetchrow(
                    """
                    SELECT discord_user_id, accepted_distance, statistics
                    FROM processed_jobs
                    WHERE job_id = $1
                    FOR UPDATE;
                    """,
                    candidate["job_id"],
                )
                if (
                    job is None
                    or str(job["statistics"]).casefold() != "real"
                    or int(job["discord_user_id"])
                        != int(candidate["discord_user_id"])
                    or int(job["accepted_distance"])
                        != int(candidate["recorded_distance"])
                ):
                    continue

                user_id = int(candidate["discord_user_id"])
                delta = int(candidate["missing_distance"])
                previous_total = int(
                    await connection.fetchval(
                        """
                        SELECT real_miles
                        FROM driver_progress
                        WHERE discord_user_id = $1
                        FOR UPDATE;
                        """,
                        user_id,
                    )
                    or 0
                )
                new_total = previous_total + delta

                await connection.execute(
                    """
                    UPDATE processed_jobs
                    SET accepted_distance = $2
                    WHERE job_id = $1;
                    """,
                    candidate["job_id"],
                    int(candidate["actual_distance"]),
                )
                await connection.execute(
                    """
                    INSERT INTO driver_progress (
                        discord_user_id, trucksbook_name, real_miles, updated_at
                    )
                    VALUES ($1, $2, $3, NOW())
                    ON CONFLICT (discord_user_id) DO UPDATE SET
                        real_miles = driver_progress.real_miles + $3,
                        updated_at = NOW();
                    """,
                    user_id,
                    candidate["trucksbook_name"],
                    delta,
                )

                # Record repaired crossings as already announced. This keeps
                # milestone history coherent without flooding the channel.
                for milestone_miles, _, _ in PROGRESSION_ROLES:
                    if previous_total < milestone_miles <= new_total:
                        await connection.execute(
                            """
                            INSERT INTO announced_milestones (
                                discord_user_id, milestone_miles,
                                trucksbook_name, job_id, announced_at
                            )
                            VALUES ($1, $2, $3, $4, NOW())
                            ON CONFLICT (discord_user_id, milestone_miles)
                            DO NOTHING;
                            """,
                            user_id,
                            milestone_miles,
                            candidate["trucksbook_name"],
                            candidate["job_id"],
                        )

                await connection.execute(
                    """
                    UPDATE mileage_repair_candidates
                    SET applied_at = NOW(), applied_by = $2
                    WHERE job_id = $1 AND applied_at IS NULL;
                    """,
                    candidate["job_id"],
                    ctx.author.id,
                )
                affected_users.add(user_id)
                applied_jobs += 1
                applied_miles += delta

    role_failures = 0
    for user_id in affected_users:
        try:
            async with db_pool.acquire() as connection:
                total = await connection.fetchval(
                    "SELECT real_miles FROM driver_progress "
                    "WHERE discord_user_id = $1;",
                    user_id,
                )
            await sync_member_progression_role(ctx.guild, user_id, int(total))
        except Exception as error:
            role_failures += 1
            print(f"REPAIR ROLE SYNC ERROR for {user_id}: {error}")

    achievements_sent, achievement_failures = (
        await announce_pending_repair_achievements(ctx.guild)
    )

    await ctx.send(
        "✅ **Mileage repair complete.**\n"
        f"Corrected jobs: `{applied_jobs:,}`\n"
        f"Mileage restored: `{applied_miles:,}`\n"
        f"Drivers updated: `{len(affected_users):,}`\n"
        f"Role-sync failures: `{role_failures:,}`\n"
        f"Achievements posted: `{achievements_sent:,}`\n"
        f"Achievement-post failures: `{achievement_failures:,}`"
    )


@mileage_audit.error
@mileage_apply.error
@repair_achievements.error
async def mileage_repair_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply(
            "❌ Only a server administrator can use mileage repair commands.",
            mention_author=False,
        )
        return
    if isinstance(error, commands.MaxConcurrencyReached):
        await ctx.reply(
            "An achievement posting run is already in progress.",
            mention_author=False,
        )
        return
    print(f"MILEAGE REPAIR COMMAND ERROR: {error}")
    await ctx.reply(
        "❌ The mileage operation failed. No uncommitted database changes "
        "were retained.",
        mention_author=False,
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

def build_driver_profile_embed(target_member, trucksbook_name, real_miles):
    """Build the original read-only embed used as the safe fallback."""
    current_rank_name = get_progression_role(real_miles)[2]
    next_role = get_next_progression_role(real_miles)
    embed = discord.Embed(
        title="🌌 A&T TRANSPORT LTD — DRIVER PROFILE",
        description=f"Progression record for {target_member.mention}",
        colour=discord.Colour.from_rgb(31, 78, 121),
    )
    embed.set_thumbnail(url=target_member.display_avatar.url)
    embed.add_field(name="Discord Driver", value=target_member.mention, inline=True)
    embed.add_field(name="TrucksBook Driver", value=f"`{trucksbook_name}`", inline=True)
    embed.add_field(name="Current Progression Rank", value=f"🏅 **{current_rank_name}**", inline=False)
    embed.add_field(name="Real Miles", value=f"🚛 **{real_miles:,}**", inline=True)

    if next_role is None:
        bar, _ = build_progress_bar(real_miles, PROGRESSION_ROLES[-1][0])
        embed.add_field(
            name="Progression Status",
            value=("🌌 **Beyond Horizons achieved**\n" f"`{bar}` **100%**\n" "Maximum A&T progression rank reached."),
            inline=False,
        )
        embed.add_field(name="Miles Remaining", value="**0 — maximum rank achieved**", inline=False)
    else:
        next_required_miles, _, next_rank_name = next_role
        miles_remaining = max(next_required_miles - real_miles, 0)
        bar, percentage = build_progress_bar(real_miles, next_required_miles)
        embed.add_field(name="Next Progression Rank", value=f"🌠 **{next_rank_name}**", inline=True)
        embed.add_field(name="Miles Remaining", value=f"**{miles_remaining:,}**", inline=True)
        embed.add_field(
            name="Progress",
            value=f"`{bar}` **{percentage:.0f}%**\n**{real_miles:,} / {next_required_miles:,} Real miles**",
            inline=False,
        )
    embed.set_footer(text="A&T Transport LTD • Driven Beyond Horizons")
    return embed


def profile_font_candidates(bold=False):
    """Return local-only fonts in broad Unicode coverage order."""
    return (
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
            "/usr/share/fonts/opentype/noto/NotoSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/seguisb.ttf",
        )
        if bold
        else (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/opentype/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
        )
    )


def load_profile_font(size, bold=False):
    """Load an installed Unicode font without any network dependency."""
    candidates = profile_font_candidates(bold)
    for font_path in candidates:
        try:
            return ImageFont.truetype(font_path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def fit_profile_text(draw, text, max_width, start_size, bold=False):
    """Choose a font size that keeps user-controlled text on the card."""
    for size in range(start_size, 11, -2):
        font = load_profile_font(size, bold=bold)
        box = draw.textbbox((0, 0), str(text), font=font)
        if box[2] - box[0] <= max_width:
            return font
    return load_profile_font(11, bold=bold)


def sanitise_profile_name(value, fallback="A&T DRIVER"):
    """Make user-controlled names readable with bundled Linux fonts.

    Compatibility characters are folded to their normal equivalents and
    emoji/decorative symbols that commonly render as square boxes are removed.
    The Discord and TrucksBook values themselves are never changed.
    """
    normalised = unicodedata.normalize("NFKC", str(value or ""))
    readable = []
    for character in normalised:
        category = unicodedata.category(character)
        if category[0] in {"L", "M", "N", "P", "Z"}:
            readable.append(character)
        elif character in "&+@#":
            readable.append(character)
    cleaned = " ".join("".join(readable).split())
    # NFKC turns common mathematical/fraktur Discord styling into ordinary
    # letters (for example 𝕸𝖞𝖘𝖙𝖎𝖈 -> Mystic).  Removing variation selectors and
    # invisible formatting characters prevents broken glyph clusters on Linux.
    cleaned = "".join(
        character
        for character in cleaned
        if character not in {"\ufe0e", "\ufe0f"}
        and unicodedata.category(character) != "Cf"
    )
    return cleaned or fallback


def rounded_profile_image(source, size, radius):
    image = ImageOps.fit(source.convert("RGBA"), size, method=Image.Resampling.LANCZOS)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    image.putalpha(mask)
    return image


def profile_text(draw, position, text, font, fill, anchor=None, shadow=2):
    """Draw crisp profile text with a restrained cinematic shadow."""
    x, y = position
    if shadow:
        draw.text(
            (x + shadow, y + shadow),
            str(text),
            font=font,
            fill=(0, 4, 10, 190),
            anchor=anchor,
        )
    draw.text(position, str(text), font=font, fill=fill, anchor=anchor)


def profile_panel(draw, box, radius=24, outline=(83, 184, 212, 115)):
    """Paint one translucent, blue-steel panel."""
    draw.rounded_rectangle(
        box,
        radius=radius,
        fill=(5, 16, 29, 168),
        outline=outline,
        width=2,
    )


def build_profile_backdrop(width, height):
    """Create a dependency-free cinematic Nordic road and aurora scene."""
    backdrop = Image.new("RGBA", (width, height), (2, 8, 18, 255))
    draw = ImageDraw.Draw(backdrop, "RGBA")

    # Deep polar-night gradient.
    for y in range(height):
        blend = y / height
        draw.line(
            (0, y, width, y),
            fill=(
                int(3 + 5 * blend),
                int(10 + 15 * blend),
                int(24 + 20 * blend),
                255,
            ),
        )

    # Deterministic stars keep every render stable and inexpensive.
    for index in range(105):
        x = (index * 137 + index * index * 17) % width
        y = (index * 73 + index * index * 7) % 560
        radius = 1 + (index % 7 == 0)
        alpha = 55 + (index * 31) % 130
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(196, 231, 244, alpha))

    # Layered aurora curtains, created entirely with Pillow for Railway.
    aurora = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    aurora_draw = ImageDraw.Draw(aurora, "RGBA")
    for ribbon, colour in enumerate(((28, 236, 192), (48, 176, 238), (105, 74, 210))):
        upper = []
        lower = []
        base_y = 75 + ribbon * 58
        for x in range(-80, width + 81, 40):
            wave = math.sin((x / 155.0) + ribbon * 1.35) * (34 + ribbon * 5)
            wave += math.sin((x / 67.0) + ribbon) * 11
            upper.append((x, base_y + wave))
            lower.append((x, base_y + 115 + wave + math.sin(x / 90.0) * 24))
        aurora_draw.polygon(
            upper + list(reversed(lower)),
            fill=(*colour, 112 - ribbon * 12),
        )
        aurora_draw.line(upper, fill=(*colour, 180), width=6)
    aurora = aurora.filter(ImageFilter.GaussianBlur(18))
    backdrop.alpha_composite(aurora)

    # A cool horizon bloom adds depth without relying on a downloaded image.
    horizon = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    horizon_draw = ImageDraw.Draw(horizon, "RGBA")
    horizon_draw.ellipse((330, 250, 1270, 780), fill=(30, 142, 174, 58))
    backdrop.alpha_composite(horizon.filter(ImageFilter.GaussianBlur(75)))

    # Layered mountain silhouettes and a road provide the trucking atmosphere.
    draw = ImageDraw.Draw(backdrop, "RGBA")
    draw.polygon(
        [(0, 565), (155, 405), (270, 520), (430, 335), (585, 530),
         (760, 360), (930, 525), (1110, 320), (1315, 520), (1460, 395),
         (1600, 520), (1600, 1000), (0, 1000)],
        fill=(8, 22, 37, 235),
    )
    # Sparse snow caps make the skyline read as mountains rather than a
    # geometric gradient while keeping the foreground dark enough for text.
    draw.polygon([(350, 423), (430, 335), (510, 430), (468, 405), (430, 430), (398, 398)], fill=(143, 185, 196, 72))
    draw.polygon([(1040, 390), (1110, 320), (1182, 404), (1144, 382), (1112, 408), (1080, 374)], fill=(151, 194, 204, 76))
    draw.polygon([(1406, 449), (1460, 395), (1523, 460), (1490, 446), (1461, 462), (1438, 435)], fill=(137, 179, 192, 62))
    draw.polygon(
        [(0, 655), (205, 500), (360, 620), (565, 450), (745, 635),
         (960, 480), (1170, 625), (1390, 455), (1600, 610),
         (1600, 1000), (0, 1000)],
        fill=(4, 14, 26, 248),
    )
    draw.line(
        [(0, 655), (205, 500), (360, 620), (565, 450), (745, 635),
         (960, 480), (1170, 625), (1390, 455), (1600, 610)],
        fill=(44, 133, 157, 105),
        width=3,
    )
    draw.polygon(
        [(510, height), (724, 600), (876, 600), (1090, height)],
        fill=(2, 8, 15, 248),
    )
    draw.line((800, 640, 800, 1000), fill=(213, 235, 236, 115), width=4)
    draw.line((715, 1000, 786, 665), fill=(55, 169, 192, 70), width=3)
    draw.line((885, 1000, 814, 665), fill=(55, 169, 192, 70), width=3)
    for y, half_width in ((704, 5), (750, 8), (812, 12), (898, 18)):
        draw.rounded_rectangle(
            (800 - half_width, y, 800 + half_width, y + 20 + half_width),
            radius=4,
            fill=(224, 238, 232, 150),
        )
    draw.ellipse((756, 610, 790, 638), fill=(123, 229, 239, 95))
    draw.ellipse((810, 610, 844, 638), fill=(238, 196, 102, 90))
    return backdrop


def load_profile_badge(threshold, maximum_size):
    """Load the exact committed PNG for a configured progression rank."""
    badge_path = BADGE_DIRECTORY / PROGRESSION_BADGES[threshold]
    if not badge_path.is_file():
        raise FileNotFoundError(f"Progression badge not found: {badge_path}")
    with Image.open(badge_path) as badge_source:
        badge = badge_source.convert("RGBA")
        badge.thumbnail(maximum_size, Image.Resampling.LANCZOS)
    return badge


def render_driver_profile_png(avatar_bytes, discord_name, trucksbook_name, real_miles):
    """Render the legacy cinematic card from read-only values."""
    if Image is None:
        raise RuntimeError("Pillow is not installed")

    current_miles, _, current_rank_name = get_progression_role(real_miles)
    next_role = get_next_progression_role(real_miles)
    width, height = 1600, 1000
    card = build_profile_backdrop(width, height)

    # Dark veil and metallic outer frame keep text legible over the scenery.
    veil = Image.new("RGBA", (width, height), (1, 8, 17, 80))
    card.alpha_composite(veil)
    draw = ImageDraw.Draw(card, "RGBA")
    draw.rounded_rectangle((20, 20, 1580, 980), 30, fill=(2, 10, 21, 100), outline=(72, 205, 226, 205), width=3)
    draw.rounded_rectangle((28, 28, 1572, 972), 26, outline=(191, 151, 63, 105), width=1)

    white = (235, 244, 247, 255)
    ice = (104, 215, 230, 255)
    muted = (147, 178, 192, 255)
    gold = (226, 181, 75, 255)
    title_font = load_profile_font(38, bold=True)
    heading_font = load_profile_font(17, bold=True)
    value_font = load_profile_font(28, bold=True)
    small_font = load_profile_font(14)

    # Brand masthead.
    profile_text(draw, (55, 45), "A&T TRANSPORT LTD", title_font, white)
    profile_text(draw, (55, 91), "DRIVEN BEYOND HORIZONS", load_profile_font(15, bold=True), gold)
    profile_text(draw, (1545, 55), "DRIVER PROFILE", load_profile_font(18, bold=True), ice, anchor="ra")
    draw.line((55, 116, 1545, 116), fill=(69, 205, 225, 210), width=3)
    draw.line((55, 122, 1545, 122), fill=(223, 177, 69, 75), width=1)

    # Three primary zones: identity, rank hero and complete ladder.
    profile_panel(draw, (40, 145, 365, 650), radius=24)
    profile_panel(draw, (385, 145, 965, 650), radius=24)
    profile_panel(draw, (985, 145, 1560, 950), radius=24)
    profile_panel(draw, (40, 670, 965, 950), radius=24)

    # Compact driver identity, leaving the badge as the visual hero.
    with Image.open(BytesIO(avatar_bytes)) as avatar_source:
        avatar = rounded_profile_image(avatar_source, (142, 142), 71)
    avatar_glow = Image.new("RGBA", (170, 170), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(avatar_glow, "RGBA")
    glow_draw.ellipse((10, 10, 160, 160), outline=(74, 221, 235, 165), width=10)
    avatar_glow = avatar_glow.filter(ImageFilter.GaussianBlur(8))
    card.alpha_composite(avatar_glow, (118, 175))
    card.alpha_composite(avatar, (132, 189))
    draw.ellipse((128, 185, 278, 335), outline=(95, 225, 236, 240), width=4)

    discord_font = fit_profile_text(draw, discord_name, 275, 27, bold=True)
    trucksbook_font = fit_profile_text(draw, trucksbook_name, 275, 21, bold=True)
    profile_text(draw, (202, 370), discord_name, discord_font, white, anchor="ma")
    profile_text(draw, (202, 410), "DISCORD DRIVER", small_font, ice, anchor="ma", shadow=0)
    draw.line((78, 438, 327, 438), fill=(84, 154, 174, 100), width=1)
    profile_text(draw, (202, 475), trucksbook_name, trucksbook_font, white, anchor="ma")
    profile_text(draw, (202, 509), "LINKED TRUCKSBOOK NAME", small_font, muted, anchor="ma", shadow=0)
    profile_text(draw, (202, 568), f"{real_miles:,}", load_profile_font(34, bold=True), gold, anchor="ma")
    profile_text(draw, (202, 610), "LIVE REAL MILES", heading_font, ice, anchor="ma", shadow=0)

    # Current-rank centerpiece uses the exact committed badge artwork.
    current_badge = load_profile_badge(current_miles, (315, 275))
    badge_x = 675 - current_badge.width // 2
    badge_y = 179 + (275 - current_badge.height) // 2
    glow = Image.new("RGBA", (390, 330), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow, "RGBA")
    glow_draw.ellipse((55, 35, 335, 305), fill=(24, 181, 214, 68))
    glow = glow.filter(ImageFilter.GaussianBlur(35))
    card.alpha_composite(glow, (480, 150))
    card.alpha_composite(current_badge, (badge_x, badge_y))
    profile_text(draw, (675, 166), "CURRENT PROGRESSION RANK", heading_font, ice, anchor="ma", shadow=0)
    rank_font = fit_profile_text(draw, current_rank_name.upper(), 515, 37, bold=True)
    profile_text(draw, (675, 476), current_rank_name.upper(), rank_font, white, anchor="ma")

    if next_role is None:
        next_miles = PROGRESSION_ROLES[-1][0]
        next_name = "Maximum rank achieved"
        miles_remaining = 0
        progress = 1.0
        progress_caption = f"{real_miles:,} REAL MILES  •  LEGENDARY STATUS"
    else:
        next_miles, _, next_name = next_role
        miles_remaining = max(next_miles - real_miles, 0)
        stage_span = max(next_miles - current_miles, 1)
        progress = min(max((real_miles - current_miles) / stage_span, 0.0), 1.0)
        progress_caption = f"{real_miles:,}  /  {next_miles:,} REAL MILES"
    next_font = fit_profile_text(draw, next_name.upper(), 290, 20, bold=True)
    profile_text(draw, (430, 539), "NEXT RANK", small_font, muted, shadow=0)
    profile_text(draw, (430, 566), next_name.upper(), next_font, white)
    profile_text(draw, (919, 539), "MILES REMAINING", small_font, muted, anchor="ra", shadow=0)
    profile_text(draw, (919, 566), f"{miles_remaining:,}", value_font, gold, anchor="ra")

    bar_left, bar_top, bar_right, bar_bottom = 430, 596, 920, 618
    draw.rounded_rectangle((bar_left - 3, bar_top - 3, bar_right + 3, bar_bottom + 3), 14, fill=(35, 99, 119, 75))
    draw.rounded_rectangle((bar_left, bar_top, bar_right, bar_bottom), 11, fill=(15, 43, 59, 245), outline=(78, 151, 174, 180), width=1)
    filled_right = bar_left + int((bar_right - bar_left) * progress)
    if filled_right > bar_left:
        draw.rounded_rectangle((bar_left, bar_top, max(filled_right, bar_left + 22), bar_bottom), 11, fill=(46, 211, 220, 255))
        draw.line((bar_left + 10, bar_top + 4, max(filled_right - 10, bar_left + 12), bar_top + 4), fill=(188, 255, 250, 180), width=2)
    profile_text(draw, (bar_left, 626), progress_caption, load_profile_font(13, bold=True), muted, shadow=0)
    profile_text(draw, (bar_right, 626), f"{progress * 100:.1f}%", load_profile_font(14, bold=True), gold, anchor="ra", shadow=0)

    # Recent earned milestones are intentionally large; the ladder records all ranks.
    earned = [role for role in PROGRESSION_ROLES if role[0] <= real_miles]
    shown = earned[-6:]
    profile_text(draw, (75, 694), "EARNED MILESTONES", load_profile_font(20, bold=True), ice)
    profile_text(draw, (930, 699), f"{len(earned)} UNLOCKED", load_profile_font(14, bold=True), gold, anchor="ra", shadow=0)
    slot_width = 140
    start_x = 72
    for index, (threshold, _, rank_name) in enumerate(shown):
        centre_x = start_x + index * slot_width + 62
        earned_badge = load_profile_badge(threshold, (112, 112))
        card.alpha_composite(earned_badge, (centre_x - earned_badge.width // 2, 735 + (112 - earned_badge.height) // 2))
        milestone_font = fit_profile_text(draw, rank_name, 128, 13, bold=True)
        profile_text(draw, (centre_x, 858), rank_name.upper(), milestone_font, white, anchor="ma", shadow=1)
        profile_text(draw, (centre_x, 883), f"{threshold:,} MI", load_profile_font(13, bold=True), gold, anchor="ma", shadow=0)
    if len(earned) > len(shown):
        profile_text(draw, (75, 922), f"+ {len(earned) - len(shown)} EARLIER MILESTONES RECORDED", load_profile_font(13, bold=True), muted, shadow=0)

    # Complete illustrated company-wide ladder, matching the reference's gallery.
    profile_text(draw, (1020, 170), "PROGRESSION RANKS", load_profile_font(22, bold=True), white)
    profile_text(draw, (1525, 174), "0 — 1,000,000 REAL MILES", load_profile_font(13, bold=True), gold, anchor="ra", shadow=0)
    draw.line((1020, 207, 1525, 207), fill=(76, 184, 204, 150), width=2)
    for index, (threshold, _, rank_name) in enumerate(PROGRESSION_ROLES):
        column = index % 5
        row = index // 5
        left = 1007 + column * 106
        top = 224 + row * 139
        centre_x = left + 48
        is_current = threshold == current_miles
        is_earned = threshold <= real_miles
        if is_current:
            draw.rounded_rectangle((left - 4, top - 5, left + 100, top + 128), 12, fill=(20, 105, 126, 120), outline=(91, 227, 233, 235), width=2)
        ladder_badge = load_profile_badge(threshold, (88, 86))
        if not is_earned:
            dimmer = Image.new("RGBA", ladder_badge.size, (3, 10, 18, 105))
            ladder_badge = Image.alpha_composite(ladder_badge, dimmer)
        card.alpha_composite(
            ladder_badge,
            (centre_x - ladder_badge.width // 2, top + (86 - ladder_badge.height) // 2),
        )
        threshold_colour = gold if is_current else ((89, 222, 202, 255) if is_earned else muted)
        draw.rounded_rectangle(
            (centre_x - 43, top + 92, centre_x + 43, top + 116),
            6,
            fill=(4, 21, 34, 225),
            outline=(49, 139, 163, 160),
            width=1,
        )
        profile_text(draw, (centre_x, top + 104), f"{threshold:,}", load_profile_font(12, bold=True), threshold_colour, anchor="mm", shadow=1)

    profile_text(draw, (1525, 925), "LIVE PROGRESSION  •  READ-ONLY", load_profile_font(12, bold=True), muted, anchor="ra", shadow=0)

    output = BytesIO()
    card.convert("RGB").save(output, format="PNG", optimize=True, compress_level=7)
    output.seek(0)
    return output


def render_driver_profile_v5_png(avatar_bytes, discord_name, trucksbook_name, real_miles):
    """Render the final cinematic profile from read-only driver values."""
    if Image is None:
        raise RuntimeError("Pillow is not installed")

    current_miles, _, current_rank_name = get_progression_role(real_miles)
    next_role = get_next_progression_role(real_miles)
    width, height = 1600, 1000
    card = build_profile_backdrop(width, height)
    # Preserve the full-canvas scenery; only a light vignette is applied here.
    vignette = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    vignette_draw = ImageDraw.Draw(vignette, "RGBA")
    for inset in range(0, 180, 12):
        alpha = max(0, 24 - inset // 9)
        vignette_draw.rounded_rectangle(
            (inset, inset, width - inset, height - inset),
            radius=40,
            outline=(0, 3, 10, alpha),
            width=24,
        )
    card.alpha_composite(vignette)
    draw = ImageDraw.Draw(card, "RGBA")

    white = (238, 246, 249, 255)
    ice = (91, 218, 242, 255)
    muted = (157, 186, 199, 255)
    gold = (238, 190, 82, 255)
    teal = (56, 226, 210, 255)

    draw.rounded_rectangle((14, 14, 1586, 986), 28, outline=(55, 177, 219, 215), width=3)
    draw.rounded_rectangle((22, 22, 1578, 978), 23, outline=(218, 175, 77, 115), width=1)
    # Frosted glass is deliberately local, leaving the aurora and mountain
    # skyline visible across the poster instead of recreating a dashboard.
    draw.rounded_rectangle((43, 39, 947, 139), 20, fill=(2, 10, 20, 102), outline=(83, 195, 218, 95), width=1)
    draw.rounded_rectangle((43, 163, 358, 677), 24, fill=(2, 10, 20, 118), outline=(83, 195, 218, 100), width=1)
    draw.rounded_rectangle((376, 555, 947, 758), 24, fill=(2, 10, 20, 112), outline=(83, 195, 218, 100), width=1)
    draw.rounded_rectangle((43, 770, 947, 960), 22, fill=(2, 10, 20, 132), outline=(83, 195, 218, 105), width=1)
    draw.rounded_rectangle((974, 24, 1574, 976), 24, fill=(1, 9, 18, 128), outline=(41, 142, 178, 145), width=2)

    profile_text(draw, (72, 58), "A&T TRANSPORT LTD", load_profile_font(32, True), white)
    profile_text(draw, (73, 101), "DRIVEN BEYOND HORIZONS  •  DRIVER PROFILE", load_profile_font(14, True), ice, shadow=0)
    draw.line((73, 124, 918, 124), fill=(73, 205, 229, 185), width=2)

    with Image.open(BytesIO(avatar_bytes)) as avatar_source:
        avatar = rounded_profile_image(avatar_source, (132, 132), 66)
    avatar_glow = Image.new("RGBA", (172, 172), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(avatar_glow, "RGBA")
    glow_draw.ellipse((14, 14, 158, 158), outline=(57, 226, 238, 205), width=12)
    card.alpha_composite(avatar_glow.filter(ImageFilter.GaussianBlur(10)), (114, 181))
    card.alpha_composite(avatar, (134, 201))
    draw.ellipse((130, 197, 270, 337), outline=(121, 236, 242, 245), width=4)

    safe_discord_name = sanitise_profile_name(discord_name)
    safe_trucksbook_name = sanitise_profile_name(trucksbook_name, "UNLINKED DRIVER")
    profile_text(draw, (200, 365), "DISCORD DRIVER", load_profile_font(13, True), ice, anchor="ma", shadow=0)
    profile_text(draw, (200, 401), safe_discord_name, fit_profile_text(draw, safe_discord_name, 270, 38, True), white, anchor="ma")
    draw.line((78, 445, 323, 445), fill=(91, 197, 214, 130), width=1)
    profile_text(draw, (200, 473), "TRUCKSBOOK DRIVER", load_profile_font(12, True), ice, anchor="ma", shadow=0)
    profile_text(draw, (200, 504), safe_trucksbook_name, fit_profile_text(draw, safe_trucksbook_name, 270, 24, True), white, anchor="ma")
    profile_text(draw, (200, 558), "CURRENT REAL MILES", load_profile_font(13, True), ice, anchor="ma", shadow=0)
    profile_text(draw, (200, 596), f"{real_miles:,}", fit_profile_text(draw, f"{real_miles:,}", 275, 56, True), gold, anchor="ma")
    profile_text(draw, (200, 650), "REAL MILES", load_profile_font(19, True), white, anchor="ma")

    current_badge = load_profile_badge(current_miles, (455, 405))
    hero_x = 659
    badge_x = hero_x - current_badge.width // 2
    badge_y = 125 + (405 - current_badge.height) // 2
    glow = Image.new("RGBA", (520, 500), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow, "RGBA")
    glow_draw.ellipse((55, 25, 465, 465), fill=(24, 190, 221, 118))
    glow_draw.ellipse((115, 70, 405, 425), fill=(236, 184, 68, 38))
    card.alpha_composite(glow.filter(ImageFilter.GaussianBlur(44)), (399, 105))
    card.alpha_composite(current_badge, (badge_x, badge_y))
    profile_text(draw, (hero_x, 151), "CURRENT PROGRESSION RANK", load_profile_font(14, True), ice, anchor="ma", shadow=0)
    profile_text(draw, (hero_x, 510), current_rank_name.upper(), fit_profile_text(draw, current_rank_name.upper(), 535, 46, True), white, anchor="ma")
    profile_text(draw, (hero_x, 568), f"{real_miles:,}", fit_profile_text(draw, f"{real_miles:,}", 510, 62, True), gold, anchor="ma")
    profile_text(draw, (hero_x, 625), "REAL MILES", load_profile_font(18, True), white, anchor="ma", shadow=0)

    if next_role is None:
        next_miles = PROGRESSION_ROLES[-1][0]
        next_name = "Maximum rank achieved"
        miles_remaining = 0
        progress = 1.0
        progress_caption = f"{real_miles:,} REAL MILES  •  LEGENDARY STATUS"
    else:
        next_miles, _, next_name = next_role
        miles_remaining = max(next_miles - real_miles, 0)
        stage_span = max(next_miles - current_miles, 1)
        progress = min(max((real_miles - current_miles) / stage_span, 0.0), 1.0)
        progress_caption = f"{real_miles:,}  /  {next_miles:,} REAL MILES"

    profile_text(draw, (407, 650), "NEXT RANK", load_profile_font(12, True), ice, shadow=0)
    profile_text(draw, (407, 677), next_name.upper(), fit_profile_text(draw, next_name.upper(), 320, 23, True), white)
    profile_text(draw, (916, 650), "MILES REMAINING", load_profile_font(12, True), ice, anchor="ra", shadow=0)
    profile_text(draw, (916, 677), f"{miles_remaining:,}", load_profile_font(27, True), gold, anchor="ra")

    bar_left, bar_top, bar_right, bar_bottom = 407, 710, 916, 735
    draw.rounded_rectangle((bar_left - 4, bar_top - 4, bar_right + 4, bar_bottom + 4), 16, fill=(31, 142, 171, 90))
    draw.rounded_rectangle((bar_left, bar_top, bar_right, bar_bottom), 13, fill=(8, 29, 43, 245), outline=(98, 197, 218, 220), width=2)
    filled_right = bar_left + int((bar_right - bar_left) * progress)
    if filled_right > bar_left:
        draw.rounded_rectangle((bar_left, bar_top, max(filled_right, bar_left + 27), bar_bottom), 13, fill=(48, 219, 223, 255))
        draw.line((bar_left + 12, bar_top + 5, max(filled_right - 10, bar_left + 14), bar_top + 5), fill=(215, 255, 251, 205), width=3)
    profile_text(draw, (bar_left, 741), progress_caption, load_profile_font(12, True), white, shadow=1)
    profile_text(draw, (bar_right, 741), f"{progress * 100:.1f}%", load_profile_font(17, True), gold, anchor="ra")

    milestone_thresholds = (25000, 50000, 100000, 500000, 1000000)
    profile_text(draw, (69, 787), "CAREER MILESTONES", load_profile_font(20, True), white)
    unlocked_milestones = sum(real_miles >= threshold for threshold in milestone_thresholds)
    profile_text(draw, (920, 791), f"{unlocked_milestones} / 5 UNLOCKED", load_profile_font(13, True), teal, anchor="ra", shadow=0)
    role_names = {threshold: rank_name for threshold, _, rank_name in PROGRESSION_ROLES}
    for index, threshold in enumerate(milestone_thresholds):
        centre_x = 136 + index * 177
        is_earned = real_miles >= threshold
        milestone_badge = load_profile_badge(threshold, (145, 112))
        if not is_earned:
            greyscale = ImageOps.grayscale(milestone_badge).convert("RGBA")
            greyscale.putalpha(milestone_badge.getchannel("A").point(lambda alpha: int(alpha * 0.52)))
            milestone_badge = greyscale
        else:
            earned_glow = Image.new("RGBA", (160, 126), (0, 0, 0, 0))
            earned_glow_draw = ImageDraw.Draw(earned_glow, "RGBA")
            earned_glow_draw.ellipse((18, 12, 142, 118), fill=(35, 211, 220, 72))
            card.alpha_composite(earned_glow.filter(ImageFilter.GaussianBlur(18)), (centre_x - 80, 808))
        card.alpha_composite(
            milestone_badge,
            (centre_x - milestone_badge.width // 2, 810 + (112 - milestone_badge.height) // 2),
        )
        label_colour = white if is_earned else muted
        threshold_colour = gold if is_earned else (104, 122, 132, 255)
        profile_text(draw, (centre_x, 926), role_names[threshold].upper(), fit_profile_text(draw, role_names[threshold].upper(), 165, 13, True), label_colour, anchor="ma", shadow=1)
        profile_text(draw, (centre_x, 949), f"{threshold:,} MI", load_profile_font(12, True), threshold_colour, anchor="ma", shadow=0)

    profile_text(draw, (1274, 48), "PATH TO IMMORTAL", load_profile_font(27, True), white, anchor="ma")
    profile_text(draw, (1274, 84), "0 — 1,000,000 REAL MILES", load_profile_font(15, True), gold, anchor="ma", shadow=1)
    draw.line((1006, 114, 1541, 114), fill=(76, 195, 219, 190), width=2)
    for index, (threshold, _, rank_name) in enumerate(PROGRESSION_ROLES):
        column, row = index % 5, index // 5
        left, top = 992 + column * 111, 128 + row * 157
        centre_x = left + 50
        is_current = threshold == current_miles
        is_earned = threshold <= real_miles
        if is_current:
            draw.rounded_rectangle((left - 3, top - 4, left + 103, top + 145), 13, fill=(15, 94, 121, 105), outline=(93, 231, 239, 240), width=2)
        elif is_earned:
            draw.rounded_rectangle((left - 2, top - 3, left + 102, top + 144), 13, fill=(8, 51, 64, 45), outline=(56, 177, 183, 90), width=1)
        ladder_badge = load_profile_badge(threshold, (99, 112))
        if not is_earned:
            greyscale = ImageOps.grayscale(ladder_badge).convert("RGBA")
            greyscale.putalpha(ladder_badge.getchannel("A").point(lambda alpha: int(alpha * 0.46)))
            ladder_badge = greyscale
        card.alpha_composite(ladder_badge, (centre_x - ladder_badge.width // 2, top + (112 - ladder_badge.height) // 2))
        threshold_colour = gold if is_current else (teal if is_earned else muted)
        draw.rounded_rectangle((centre_x - 43, top + 116, centre_x + 43, top + 140), 6, fill=(4, 21, 34, 178), outline=(49, 139, 163, 145), width=1)
        profile_text(draw, (centre_x, top + 128), f"{threshold:,}", load_profile_font(12, True), threshold_colour, anchor="mm", shadow=1)

    draw.line((1014, 914, 1535, 914), fill=(73, 180, 207, 120), width=1)
    profile_text(draw, (1274, 935), "DRIVEN BEYOND HORIZONS", load_profile_font(13, True), ice, anchor="ma", shadow=0)
    profile_text(draw, (1274, 957), "TOGETHER WE DRIVE  •  TOGETHER WE CONQUER", load_profile_font(10, True), muted, anchor="ma", shadow=0)

    output = BytesIO()
    card.convert("RGB").save(output, format="PNG", optimize=True, compress_level=7)
    output.seek(0)
    return output

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

    fallback_embed = build_driver_profile_embed(
        target_member,
        trucksbook_name,
        real_miles,
    )

    try:
        avatar_bytes = await target_member.display_avatar.with_size(256).read()
        profile_png = await asyncio.to_thread(
            render_driver_profile_v5_png,
            avatar_bytes,
            target_member.display_name,
            trucksbook_name,
            real_miles,
        )
        await ctx.send(
            file=discord.File(
                profile_png,
                filename="at-driver-profile.png",
            )
        )
    except Exception as error:
        print(f"GRAPHICAL PROFILE FALLBACK: {error}")
        await ctx.send(embed=fallback_embed)


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
