import os
import re
import asyncio
import math
import sys
import unicodedata
import json
from datetime import date, datetime, time, timedelta, timezone
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
import asyncpg
import discord
from bs4 import BeautifulSoup
from discord.ext import commands, tasks

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

HALL_OF_FAME_CHANNEL_ID = optional_snowflake_env(
    "HALL_OF_FAME_CHANNEL_ID"
)
MILEAGE_CLUB_40K_CHANNEL_ID = optional_snowflake_env(
    "MILEAGE_CLUB_40K_CHANNEL_ID"
)
DRIVER_LEADERBOARD_CHANNEL_ID = optional_snowflake_env(
    "DRIVER_LEADERBOARD_CHANNEL_ID"
)
WEEKLY_CHAMPION_ROLE_ID = optional_snowflake_env(
    "WEEKLY_CHAMPION_ROLE_ID"
)
DRIVER_OF_THE_MONTH_ROLE_ID = optional_snowflake_env(
    "DRIVER_OF_THE_MONTH_ROLE_ID"
)

UK_TIMEZONE = ZoneInfo("Europe/London")


def first_optional_snowflake_env(*variable_names):
    """Return the first configured ID from a group of compatible names."""
    for variable_name in variable_names:
        if os.getenv(variable_name, "").strip():
            return optional_snowflake_env(variable_name)
    return None


# Achievement roles are cumulative and permanent while the driver is in the
# server. Multiple names are accepted to remain compatible with the Railway
# variables used during planning and with the conventional *_ROLE_ID spelling.
ACHIEVEMENT_ROLES = [
    (40000, first_optional_snowflake_env(
        "ACHIEVEMENT_40K_ROLE_ID", "ACHIEVEMENT_ROLE_40K",
        "ROLE_ACHIEVEMENT_40K", "ROLE_40K_ACHIEVEMENT",
        "ROLE_MILEAGE_CLUB_40K", "ROLE_40K_MILEAGE_CLUB"),
     "40K Mileage Club"),
    (100000, first_optional_snowflake_env(
        "ACHIEVEMENT_100K_ROLE_ID", "ACHIEVEMENT_ROLE_100K",
        "ROLE_ACHIEVEMENT_100K", "ROLE_100K_ACHIEVEMENT",
        "ROLE_MILEAGE_CLUB_100K"),
     "100K Achievement"),
    (250000, first_optional_snowflake_env(
        "ACHIEVEMENT_250K_ROLE_ID", "ACHIEVEMENT_ROLE_250K",
        "ROLE_ACHIEVEMENT_250K", "ROLE_250K_ACHIEVEMENT",
        "ROLE_MILEAGE_CLUB_250K"),
     "250K Achievement"),
    (500000, first_optional_snowflake_env(
        "ACHIEVEMENT_500K_ROLE_ID", "ACHIEVEMENT_ROLE_500K",
        "ROLE_ACHIEVEMENT_500K", "ROLE_500K_ACHIEVEMENT",
        "ROLE_MILEAGE_CLUB_500K"),
     "500K Achievement"),
    (750000, first_optional_snowflake_env(
        "ACHIEVEMENT_750K_ROLE_ID", "ACHIEVEMENT_ROLE_750K",
        "ROLE_ACHIEVEMENT_750K", "ROLE_750K_ACHIEVEMENT",
        "ROLE_MILEAGE_CLUB_750K"),
     "750K Achievement"),
    (1000000, first_optional_snowflake_env(
        "ACHIEVEMENT_1M_ROLE_ID", "ACHIEVEMENT_ROLE_1M",
        "ROLE_ACHIEVEMENT_1M", "ROLE_1M_ACHIEVEMENT",
        "ROLE_MILEAGE_CLUB_1M"),
     "1M Achievement"),
]

# Hall of Fame roles are highest-tier-only. These are deliberately separate
# from both normal progression roles and permanent cumulative achievements.
HALL_OF_FAME_ROLES = [
    (250000, first_optional_snowflake_env(
        "HALL_OF_FAME_VETERAN_ROLE_ID", "HOF_VETERAN_ROLE_ID",
        "ROLE_HOF_VETERAN", "ROLE_HOF_VETERAN_250K",
        "ROLE_HALL_OF_FAME_VETERAN"), "Veteran"),
    (500000, first_optional_snowflake_env(
        "HALL_OF_FAME_ELITE_ROLE_ID", "HOF_ELITE_ROLE_ID",
        "ROLE_HOF_ELITE", "ROLE_HOF_ELITE_500K",
        "ROLE_HALL_OF_FAME_ELITE"), "Elite"),
    (1000000, first_optional_snowflake_env(
        "HALL_OF_FAME_IMMORTAL_ROLE_ID", "HOF_IMMORTAL_ROLE_ID",
        "ROLE_HOF_IMMORTAL", "ROLE_HOF_IMMORTAL_1M",
        "ROLE_HALL_OF_FAME_IMMORTAL"), "Immortal"),
]

PERMANENT_SYNC_LOCK = asyncio.Lock()
LIVE_DISPLAY_LOCK = asyncio.Lock()
LEADERBOARD_UPDATE_LOCK = asyncio.Lock()
permanent_history_synced = False


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
PROFILE_TEMPLATE_PATH = (
    Path(__file__).resolve().parent
    / "assets"
    / "profile"
    / "driver-profile-template.png"
)
PROFILE_ICON_ATLAS_PATH = (
    Path(__file__).resolve().parent
    / "assets" / "profile" / "icons" / "at-profile-icons.png"
)
PROFILE_PRESTIGE_ATLAS_PATH = (
    Path(__file__).resolve().parent
    / "assets" / "profile" / "prestige" / "at-prestige-insignias.png"
)
PROFILE_FONT_DIRECTORY = Path(__file__).resolve().parent / "assets" / "profile" / "fonts"
PROFILE_FONT_REGULAR_PATH = PROFILE_FONT_DIRECTORY / "LiberationSans-Regular.ttf"
PROFILE_FONT_BOLD_PATH = PROFILE_FONT_DIRECTORY / "LiberationSans-Bold.ttf"


# --------------------------------------------------
# ROLE CATEGORY DIVIDERS
# --------------------------------------------------

# Discord has no native parent/child role relationship. These decorative
# divider roles are kept in sync by the bot: a member receives a divider while
# they hold any role in that category and loses it after the last match goes.
ROLE_CATEGORY_DIVIDERS = {
    "━━ 👑 LEADERSHIP & STAFF ━━": {
        "role_names": {
            "Owners", "Owner", "Admin", "Administrator", "Management",
            "Recruitment Manager", "Driver Dispatcher", "Server Moderators",
            "Server Moderator", "Moderator",
        },
    },
    "━━ 🏅 DRIVER RANKS & BADGES ━━": {
        "role_names": set(),
        "role_ids": {
            role_id for _, role_id, _ in (
                PROGRESSION_ROLES + ACHIEVEMENT_ROLES + HALL_OF_FAME_ROLES
            ) if role_id
        } | {
            role_id for role_id in (
                WEEKLY_CHAMPION_ROLE_ID, DRIVER_OF_THE_MONTH_ROLE_ID,
            ) if role_id
        },
    },
    "━━ 🤝 PARTNERS & COMMUNITY ━━": {
        "role_names": {
            "Alliance Partners", "Alliance Partner", "Community Partner",
            "Partners",
        },
    },
    "━━ 🎮 INTERESTS & ACCESS ━━": {
        "role_names": {"Other Games", "New Member", "F1", "Formula 1"},
    },
    "━━ 💜 BOOSTERS & EXTRAS ━━": {
        "role_names": {"Server Booster", "Booster"},
        "include_boosters": True,
    },
    "━━ 🤖 BOTS & INTEGRATIONS ━━": {
        "role_names": {"Bots", "Bot", "Integrations"},
        "include_bots": True,
    },
    "━━ 🔞 AGE ROLES ━━": {
        "role_names": {"Adult", "Adolescent", "Teenager", "Teen", "Child"},
    },
}


def normalise_role_name(value):
    """Return a stable comparison form for a Discord role name."""
    return " ".join(
        unicodedata.normalize("NFKC", str(value)).casefold().split()
    )


def member_matches_role_category(member, configuration):
    """Return whether a member currently belongs to a divider category."""
    member_roles = list(getattr(member, "roles", []))
    member_role_ids = {role.id for role in member_roles}
    member_role_names = {
        normalise_role_name(role.name) for role in member_roles
    }
    configured_names = {
        normalise_role_name(name)
        for name in configuration.get("role_names", set())
    }

    return bool(
        member_role_ids & configuration.get("role_ids", set())
        or member_role_names & configured_names
        or configuration.get("include_bots", False) and member.bot
        or configuration.get("include_boosters", False)
        and member.premium_since is not None
    )


async def sync_member_role_category_dividers(member):
    """Synchronise all decorative category roles for one guild member."""
    if member.guild.id != GUILD_ID:
        return {"added": 0, "removed": 0, "missing": []}

    roles_by_name = {
        normalise_role_name(role.name): role for role in member.guild.roles
    }
    add_roles = []
    remove_roles = []
    missing = []

    for divider_name, configuration in ROLE_CATEGORY_DIVIDERS.items():
        divider = roles_by_name.get(normalise_role_name(divider_name))
        if divider is None:
            missing.append(divider_name)
            continue

        should_have = member_matches_role_category(member, configuration)
        has_divider = divider in member.roles
        if should_have and not has_divider:
            add_roles.append(divider)
        elif not should_have and has_divider:
            remove_roles.append(divider)

    if remove_roles:
        await member.remove_roles(
            *remove_roles, reason="A&T role category divider sync"
        )
    if add_roles:
        await member.add_roles(
            *add_roles, reason="A&T role category divider sync"
        )

    return {
        "added": len(add_roles),
        "removed": len(remove_roles),
        "missing": missing,
    }


async def sync_all_role_category_dividers(guild):
    """Repair divider roles for every cached member at bot startup."""
    added = 0
    removed = 0
    missing = set()
    failures = 0

    for member in guild.members:
        try:
            result = await sync_member_role_category_dividers(member)
            added += result["added"]
            removed += result["removed"]
            missing.update(result["missing"])
        except (discord.Forbidden, discord.HTTPException) as error:
            failures += 1
            print(f"ROLE DIVIDER SYNC ERROR: {member.id}: {error}")

    print(
        "Role category divider sync complete: "
        f"{added} added, {removed} removed, {failures} failed."
    )
    if missing:
        print(
            "ROLE DIVIDER WARNING: Missing divider role(s): "
            + ", ".join(sorted(missing))
        )


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
        # PERMANENT ACHIEVEMENTS / HALL OF FAME
        # ------------------------------------------

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS permanent_achievements (
                discord_user_id BIGINT NOT NULL,
                achievement_miles BIGINT NOT NULL,
                trucksbook_name TEXT NOT NULL,
                earned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (discord_user_id, achievement_miles)
            );

            CREATE SEQUENCE IF NOT EXISTS immortal_registry_number_seq
                AS BIGINT START WITH 1;

            CREATE TABLE IF NOT EXISTS hall_of_fame (
                discord_user_id BIGINT PRIMARY KEY,
                trucksbook_name TEXT NOT NULL,
                highest_tier TEXT NOT NULL
                    CHECK (highest_tier IN ('Veteran', 'Elite', 'Immortal')),
                highest_verified_real_miles BIGINT NOT NULL,
                veteran_since TIMESTAMPTZ NOT NULL,
                elite_since TIMESTAMPTZ,
                immortal_since TIMESTAMPTZ,
                immortal_number BIGINT UNIQUE,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                last_seen_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS bot_controlled_messages (
                purpose TEXT PRIMARY KEY,
                channel_id BIGINT NOT NULL,
                message_id BIGINT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_hall_of_fame_tier
                ON hall_of_fame(highest_tier, highest_verified_real_miles DESC);

            CREATE TABLE IF NOT EXISTS leaderboard_periods (
                period_type TEXT NOT NULL
                    CHECK (period_type IN ('weekly', 'monthly')),
                period_start DATE NOT NULL,
                period_end DATE NOT NULL,
                winner_discord_user_id BIGINT,
                winner_trucksbook_name TEXT,
                winner_real_miles BIGINT,
                rankings JSONB NOT NULL DEFAULT '[]'::jsonb,
                finalized_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (period_type, period_start),
                CHECK (period_end > period_start)
            );

            CREATE TABLE IF NOT EXISTS competition_wins (
                period_type TEXT NOT NULL
                    CHECK (period_type IN ('weekly', 'monthly')),
                period_start DATE NOT NULL,
                discord_user_id BIGINT NOT NULL,
                trucksbook_name TEXT NOT NULL,
                real_miles BIGINT NOT NULL CHECK (real_miles > 0),
                awarded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (period_type, period_start),
                FOREIGN KEY (period_type, period_start)
                    REFERENCES leaderboard_periods(period_type, period_start)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_processed_jobs_leaderboard
                ON processed_jobs(processed_at, discord_user_id)
                WHERE LOWER(statistics) = 'real';

            CREATE INDEX IF NOT EXISTS idx_competition_wins_driver
                ON competition_wins(discord_user_id, period_type);
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
    print("Permanent achievements and Hall of Fame tables ready.")
    print("Automated driver leaderboard tables ready.")


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
# PERMANENT ACHIEVEMENTS / HALL OF FAME
# --------------------------------------------------

def get_hall_of_fame_tier(real_miles):
    """Return the highest earned Hall of Fame tier, or None."""
    earned = [item for item in HALL_OF_FAME_ROLES if real_miles >= item[0]]
    return earned[-1] if earned else None


async def get_discord_channel(channel_id):
    if channel_id is None:
        return None
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            return None
    return channel if hasattr(channel, "send") else None


async def reconcile_permanent_records(
    discord_user_id,
    trucksbook_name,
    real_miles,
    is_active,
):
    """Persist every earned achievement and the driver's permanent HOF record."""
    new_achievements = []
    hall_changed = False

    async with db_pool.acquire() as connection:
        async with connection.transaction():
            for threshold, _, achievement_name in ACHIEVEMENT_ROLES:
                if real_miles < threshold:
                    continue
                inserted = await connection.fetchval(
                    """
                    INSERT INTO permanent_achievements (
                        discord_user_id, achievement_miles, trucksbook_name
                    ) VALUES ($1, $2, $3)
                    ON CONFLICT (discord_user_id, achievement_miles)
                    DO UPDATE SET trucksbook_name = EXCLUDED.trucksbook_name
                    RETURNING (xmax = 0);
                    """,
                    discord_user_id,
                    threshold,
                    trucksbook_name,
                )
                if inserted:
                    new_achievements.append(
                        {"miles": threshold, "name": achievement_name}
                    )

            tier = get_hall_of_fame_tier(real_miles)
            if tier is not None:
                _, _, tier_name = tier
                existing = await connection.fetchrow(
                    """
                    SELECT highest_tier, immortal_number
                    FROM hall_of_fame
                    WHERE discord_user_id = $1
                    FOR UPDATE;
                    """,
                    discord_user_id,
                )
                previous_tier = existing["highest_tier"] if existing else None
                immortal_number = existing["immortal_number"] if existing else None
                if tier_name == "Immortal" and immortal_number is None:
                    immortal_number = await connection.fetchval(
                        "SELECT nextval('immortal_registry_number_seq');"
                    )

                veteran_since = await connection.fetchval(
                    """
                    SELECT earned_at FROM permanent_achievements
                    WHERE discord_user_id = $1 AND achievement_miles = 250000;
                    """,
                    discord_user_id,
                )
                elite_since = await connection.fetchval(
                    """
                    SELECT earned_at FROM permanent_achievements
                    WHERE discord_user_id = $1 AND achievement_miles = 500000;
                    """,
                    discord_user_id,
                )
                immortal_since = await connection.fetchval(
                    """
                    SELECT earned_at FROM permanent_achievements
                    WHERE discord_user_id = $1 AND achievement_miles = 1000000;
                    """,
                    discord_user_id,
                )

                await connection.execute(
                    """
                    INSERT INTO hall_of_fame (
                        discord_user_id, trucksbook_name, highest_tier,
                        highest_verified_real_miles, veteran_since, elite_since,
                        immortal_since, immortal_number, is_active, last_seen_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9,
                              CASE WHEN $9 THEN NOW() ELSE NULL END)
                    ON CONFLICT (discord_user_id) DO UPDATE SET
                        trucksbook_name = EXCLUDED.trucksbook_name,
                        highest_tier = EXCLUDED.highest_tier,
                        highest_verified_real_miles = GREATEST(
                            hall_of_fame.highest_verified_real_miles,
                            EXCLUDED.highest_verified_real_miles
                        ),
                        veteran_since = LEAST(
                            hall_of_fame.veteran_since, EXCLUDED.veteran_since
                        ),
                        elite_since = COALESCE(
                            hall_of_fame.elite_since, EXCLUDED.elite_since
                        ),
                        immortal_since = COALESCE(
                            hall_of_fame.immortal_since, EXCLUDED.immortal_since
                        ),
                        immortal_number = COALESCE(
                            hall_of_fame.immortal_number, EXCLUDED.immortal_number
                        ),
                        is_active = EXCLUDED.is_active,
                        last_seen_at = CASE WHEN EXCLUDED.is_active THEN NOW()
                                            ELSE hall_of_fame.last_seen_at END,
                        updated_at = NOW();
                    """,
                    discord_user_id,
                    trucksbook_name,
                    tier_name,
                    real_miles,
                    veteran_since,
                    elite_since,
                    immortal_since,
                    immortal_number,
                    is_active,
                )
                hall_changed = previous_tier != tier_name

    return {
        "new_achievements": new_achievements,
        "hall_changed": hall_changed,
        "hall_tier": get_hall_of_fame_tier(real_miles),
    }


async def sync_member_permanent_roles(guild, discord_user_id, real_miles):
    """Add cumulative achievement roles and enforce one highest HOF role."""
    member = guild.get_member(discord_user_id)
    if member is None:
        return {"status": "member_missing"}

    add_roles = []
    for threshold, role_id, _ in ACHIEVEMENT_ROLES:
        if role_id and real_miles >= threshold:
            role = guild.get_role(role_id)
            if role and role not in member.roles:
                add_roles.append(role)

    hall_tier = get_hall_of_fame_tier(real_miles)
    target_hall_role_id = hall_tier[1] if hall_tier else None
    configured_hall_ids = {
        role_id for _, role_id, _ in HALL_OF_FAME_ROLES if role_id
    }
    remove_roles = [
        role for role in member.roles
        if role.id in configured_hall_ids and role.id != target_hall_role_id
    ]
    if target_hall_role_id:
        target_role = guild.get_role(target_hall_role_id)
        if target_role and target_role not in member.roles:
            add_roles.append(target_role)

    if remove_roles:
        await member.remove_roles(
            *remove_roles, reason="A&T Hall of Fame highest-tier sync"
        )
    if add_roles:
        await member.add_roles(
            *add_roles, reason="A&T permanent achievement sync"
        )
    return {"status": "updated" if add_roles or remove_roles else "correct"}


def build_achievement_embed(member, discord_user_id, trucksbook_name, achievement, real_miles):
    threshold = achievement["miles"]
    hall_tier = get_hall_of_fame_tier(real_miles)
    driver_display = member.mention if member else f"<@{discord_user_id}>"
    embed = discord.Embed(
        title="🌟 A&T PERMANENT ACHIEVEMENT UNLOCKED",
        description=(
            f"Congratulations {driver_display}!\n\n"
            f"**{threshold:,} verified A&T Real Miles**\n"
            "This cumulative achievement is now part of your permanent record."
        ),
        colour=discord.Colour.gold(),
    )
    if member:
        embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="TrucksBook Driver", value=f"`{trucksbook_name}`")
    embed.add_field(name="Verified Real Total", value=f"**{real_miles:,} miles**")
    if hall_tier and threshold in {250000, 500000, 1000000}:
        embed.add_field(
            name="Hall of Fame",
            value=f"🏛️ **{hall_tier[2]}** — a permanent place in A&T history",
            inline=False,
        )
    if threshold == 40000 and MILEAGE_CLUB_40K_CHANNEL_ID:
        embed.add_field(
            name="40K Reward Hub Unlocked",
            value=f"Visit <#{MILEAGE_CLUB_40K_CHANNEL_ID}> for your milestone rewards.",
            inline=False,
        )
    embed.set_footer(text="A&T Transport LTD • Permanent Achievement")
    return embed


async def announce_permanent_achievement(
    guild, member, discord_user_id, trucksbook_name, achievement, real_miles
):
    channel = await get_discord_channel(MILESTONE_ANNOUNCEMENT_CHANNEL_ID)
    if channel is None:
        print("PERMANENT ACHIEVEMENT ANNOUNCEMENT SKIPPED: channel unavailable.")
        return False
    try:
        await channel.send(embed=build_achievement_embed(
            member, discord_user_id, trucksbook_name, achievement, real_miles
        ))
        return True
    except (discord.Forbidden, discord.HTTPException) as error:
        print(f"PERMANENT ACHIEVEMENT ANNOUNCEMENT ERROR: {error}")
        return False


async def upsert_controlled_message(purpose, channel_id, embeds):
    """Edit the one recorded bot-owned message, recreating only if it vanished."""
    channel = await get_discord_channel(channel_id)
    if channel is None or db_pool is None:
        return False
    async with LIVE_DISPLAY_LOCK:
        async with db_pool.acquire() as connection:
            async with connection.transaction():
                # The PostgreSQL lock also protects against overlapping Railway
                # instances during a deployment, not only tasks in this process.
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1));",
                    f"at-controlled-message-{purpose}",
                )
                row = await connection.fetchrow(
                    "SELECT channel_id, message_id FROM bot_controlled_messages "
                    "WHERE purpose = $1 FOR UPDATE;",
                    purpose,
                )
                message = None
                if row and int(row["channel_id"]) == channel.id:
                    try:
                        message = await channel.fetch_message(int(row["message_id"]))
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        message = None
                try:
                    if message is None:
                        message = await channel.send(embeds=embeds)
                    else:
                        await message.edit(content=None, embeds=embeds)
                except (discord.Forbidden, discord.HTTPException) as error:
                    print(f"CONTROLLED MESSAGE ERROR ({purpose}): {error}")
                    return False
                await connection.execute(
                    """
                    INSERT INTO bot_controlled_messages (purpose, channel_id, message_id)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (purpose) DO UPDATE SET
                        channel_id = EXCLUDED.channel_id,
                        message_id = EXCLUDED.message_id,
                        updated_at = NOW();
                    """,
                    purpose,
                    channel.id,
                    message.id,
                )
        return True


def leaderboard_period_bounds(period_type, reference=None):
    """Return the current UK-local period as [start, end) calendar dates."""
    local_now = reference or datetime.now(UK_TIMEZONE)
    local_day = local_now.date()
    if period_type == "weekly":
        start = local_day - timedelta(days=local_day.weekday())
        return start, start + timedelta(days=7)
    if period_type == "monthly":
        start = local_day.replace(day=1)
        if start.month == 12:
            end = date(start.year + 1, 1, 1)
        else:
            end = date(start.year, start.month + 1, 1)
        return start, end
    raise ValueError(f"Unsupported leaderboard period: {period_type}")


def leaderboard_period_end(period_type, period_start):
    if period_type == "weekly":
        return period_start + timedelta(days=7)
    if period_start.month == 12:
        return date(period_start.year + 1, 1, 1)
    return date(period_start.year, period_start.month + 1, 1)


def uk_midnight_utc(local_day):
    return datetime.combine(local_day, time.min, UK_TIMEZONE).astimezone(timezone.utc)


def active_driver_ids(guild):
    """Linked leaderboard eligibility is limited to members still in A&T."""
    if guild is None:
        return []
    return [member.id for member in guild.members if not member.bot]


async def fetch_period_rankings(connection, guild, period_start, period_end):
    member_ids = active_driver_ids(guild)
    if not member_ids:
        return []
    return await connection.fetch(
        """
        SELECT
            jobs.discord_user_id,
            COALESCE(links.trucksbook_name, MAX(jobs.trucksbook_name))
                AS trucksbook_name,
            SUM(jobs.accepted_distance)::BIGINT AS real_miles,
            MIN(jobs.processed_at) AS first_job_at
        FROM processed_jobs AS jobs
        LEFT JOIN driver_links AS links
            ON links.discord_user_id = jobs.discord_user_id
        WHERE LOWER(jobs.statistics) = 'real'
          AND jobs.accepted_distance > 0
          AND jobs.processed_at >= $1
          AND jobs.processed_at < $2
          AND jobs.discord_user_id = ANY($3::BIGINT[])
        GROUP BY jobs.discord_user_id, links.trucksbook_name
        HAVING SUM(jobs.accepted_distance) > 0
        ORDER BY real_miles DESC, first_job_at ASC, jobs.discord_user_id ASC;
        """,
        uk_midnight_utc(period_start),
        uk_midnight_utc(period_end),
        member_ids,
    )


async def finalize_leaderboard_period(guild, period_type, period_start):
    """Persist one completed period exactly once and permanently record its win."""
    period_end = leaderboard_period_end(period_type, period_start)
    async with db_pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1));",
                f"at-leaderboard-{period_type}-{period_start.isoformat()}",
            )
            exists = await connection.fetchval(
                """
                SELECT TRUE FROM leaderboard_periods
                WHERE period_type = $1 AND period_start = $2;
                """,
                period_type,
                period_start,
            )
            if exists:
                return False

            rows = await fetch_period_rankings(
                connection, guild, period_start, period_end
            )
            saved_rankings = [
                {
                    "rank": index,
                    "discord_user_id": int(row["discord_user_id"]),
                    "trucksbook_name": str(row["trucksbook_name"]),
                    "real_miles": int(row["real_miles"]),
                }
                for index, row in enumerate(rows, start=1)
            ]
            winner = rows[0] if rows else None
            await connection.execute(
                """
                INSERT INTO leaderboard_periods (
                    period_type, period_start, period_end,
                    winner_discord_user_id, winner_trucksbook_name,
                    winner_real_miles, rankings
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7::JSONB)
                ON CONFLICT (period_type, period_start) DO NOTHING;
                """,
                period_type,
                period_start,
                period_end,
                int(winner["discord_user_id"]) if winner else None,
                str(winner["trucksbook_name"]) if winner else None,
                int(winner["real_miles"]) if winner else None,
                json.dumps(saved_rankings),
            )
            if winner:
                await connection.execute(
                    """
                    INSERT INTO competition_wins (
                        period_type, period_start, discord_user_id,
                        trucksbook_name, real_miles
                    )
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (period_type, period_start) DO NOTHING;
                    """,
                    period_type,
                    period_start,
                    int(winner["discord_user_id"]),
                    str(winner["trucksbook_name"]),
                    int(winner["real_miles"]),
                )
    print(
        f"LEADERBOARD: Finalized {period_type} period "
        f"{period_start} to {period_end}."
    )
    return True


async def finalize_completed_leaderboard_periods(guild):
    """Catch up missed rollovers; first deployment starts with the last period."""
    if db_pool is None or guild is None:
        return
    for period_type in ("weekly", "monthly"):
        current_start, _ = leaderboard_period_bounds(period_type)
        async with db_pool.acquire() as connection:
            last_end = await connection.fetchval(
                """
                SELECT period_end FROM leaderboard_periods
                WHERE period_type = $1
                ORDER BY period_start DESC LIMIT 1;
                """,
                period_type,
            )
        if last_end is None:
            if period_type == "weekly":
                next_start = current_start - timedelta(days=7)
            else:
                previous_day = current_start - timedelta(days=1)
                next_start = previous_day.replace(day=1)
        else:
            next_start = last_end

        while next_start < current_start:
            await finalize_leaderboard_period(guild, period_type, next_start)
            next_start = leaderboard_period_end(period_type, next_start)


async def sync_competition_role(guild, period_type, role_id):
    if not role_id or guild is None or db_pool is None:
        return
    role = guild.get_role(role_id)
    if role is None:
        print(f"LEADERBOARD ROLE ERROR: Role {role_id} was not found.")
        return
    async with db_pool.acquire() as connection:
        winner_id = await connection.fetchval(
            """
            SELECT winner_discord_user_id
            FROM leaderboard_periods
            WHERE period_type = $1
            ORDER BY period_start DESC
            LIMIT 1;
            """,
            period_type,
        )
    winner = guild.get_member(int(winner_id)) if winner_id else None
    remove_from = [member for member in role.members if member != winner]
    for member in remove_from:
        try:
            await member.remove_roles(
                role, reason=f"A&T {period_type} champion rollover"
            )
        except (discord.Forbidden, discord.HTTPException) as error:
            print(f"LEADERBOARD ROLE REMOVE ERROR ({member.id}): {error}")
    if winner and role not in winner.roles:
        try:
            await winner.add_roles(
                role, reason=f"A&T {period_type} champion"
            )
        except (discord.Forbidden, discord.HTTPException) as error:
            print(f"LEADERBOARD ROLE ADD ERROR ({winner.id}): {error}")


def leaderboard_lines(rows, guild, limit, mark_former=False):
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = []
    for position, row in enumerate(rows[:limit], start=1):
        user_id = int(row["discord_user_id"])
        member = guild.get_member(user_id) if guild else None
        if member:
            label = member.mention
        else:
            safe_name = discord.utils.escape_markdown(str(row["trucksbook_name"]))
            label = f"**{safe_name}**"
            if mark_former:
                label += " *(Former Driver)*"
        prefix = medals.get(position, f"`{position:>2}.`")
        lines.append(
            f"{prefix} {label} — **{int(row['real_miles']):,} mi**"
        )
    return "\n".join(lines) if lines else "*No qualifying Real miles recorded yet.*"


async def refresh_driver_leaderboard(guild=None):
    if DRIVER_LEADERBOARD_CHANNEL_ID is None or db_pool is None:
        return False
    guild = guild or bot.get_guild(GUILD_ID)
    if guild is None:
        return False
    weekly_start, weekly_end = leaderboard_period_bounds("weekly")
    monthly_start, monthly_end = leaderboard_period_bounds("monthly")
    async with db_pool.acquire() as connection:
        weekly_rows = await fetch_period_rankings(
            connection, guild, weekly_start, weekly_end
        )
        monthly_rows = await fetch_period_rankings(
            connection, guild, monthly_start, monthly_end
        )
        all_time_rows = await connection.fetch(
            """
            SELECT discord_user_id, trucksbook_name, real_miles
            FROM driver_progress
            WHERE real_miles > 0
            ORDER BY real_miles DESC, updated_at ASC, discord_user_id ASC
            LIMIT 10;
            """
        )
    next_week = int(uk_midnight_utc(weekly_end).timestamp())
    next_month = int(uk_midnight_utc(monthly_end).timestamp())
    embed = discord.Embed(
        title="🚛 A&T TRANSPORT LTD — DRIVER LEADERBOARD",
        description=(
            "Verified **Real Miles** only. Weekly and monthly rankings show "
            "active A&T drivers; all-time records preserve former drivers."
        ),
        colour=discord.Colour.from_rgb(31, 110, 150),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(
        name="📅 Top 5 Weekly",
        value=(
            leaderboard_lines(weekly_rows, guild, 5)
            + f"\n\nResets <t:{next_week}:R>"
        ),
        inline=False,
    )
    embed.add_field(
        name="🗓️ Top 5 Monthly",
        value=(
            leaderboard_lines(monthly_rows, guild, 5)
            + f"\n\nResets <t:{next_month}:R>"
        ),
        inline=False,
    )
    embed.add_field(
        name="🏛️ Top 10 All-Time",
        value=leaderboard_lines(all_time_rows, guild, 10, mark_former=True),
        inline=False,
    )
    embed.set_footer(
        text=(
            "A&T Transport LTD • UK time • Ties: earliest qualifying job "
            "then Discord ID • Automatically updated"
        )
    )
    return await upsert_controlled_message(
        "driver_leaderboard", DRIVER_LEADERBOARD_CHANNEL_ID, [embed]
    )


async def maintain_driver_leaderboard():
    """Finalize periods, rotate holder roles, and refresh the one live display."""
    guild = bot.get_guild(GUILD_ID)
    if guild is None or db_pool is None:
        return
    async with LEADERBOARD_UPDATE_LOCK:
        await finalize_completed_leaderboard_periods(guild)
        await sync_competition_role(
            guild, "weekly", WEEKLY_CHAMPION_ROLE_ID
        )
        await sync_competition_role(
            guild, "monthly", DRIVER_OF_THE_MONTH_ROLE_ID
        )
        await refresh_driver_leaderboard(guild)


@tasks.loop(minutes=1)
async def leaderboard_rollover_task():
    try:
        await maintain_driver_leaderboard()
    except Exception as error:
        print(f"LEADERBOARD MAINTENANCE ERROR: {error}")


@leaderboard_rollover_task.before_loop
async def before_leaderboard_rollover_task():
    await bot.wait_until_ready()


async def refresh_hall_of_fame_display():
    if HALL_OF_FAME_CHANNEL_ID is None or db_pool is None:
        return False
    async with db_pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT discord_user_id, trucksbook_name, highest_tier,
                   highest_verified_real_miles, veteran_since, elite_since,
                   immortal_since, immortal_number, is_active
            FROM hall_of_fame
            ORDER BY CASE highest_tier WHEN 'Immortal' THEN 1 WHEN 'Elite' THEN 2 ELSE 3 END,
                     immortal_number NULLS LAST,
                     highest_verified_real_miles DESC,
                     veteran_since ASC;
            """
        )
    colours = {
        "Immortal": discord.Colour.from_rgb(96, 72, 170),
        "Elite": discord.Colour.gold(),
        "Veteran": discord.Colour.from_rgb(49, 120, 160),
    }
    icons = {"Immortal": "💎", "Elite": "👑", "Veteran": "⚔️"}
    embeds = []
    remaining_budget = 5200
    for tier_name in ("Immortal", "Elite", "Veteran"):
        tier_rows = [row for row in rows if row["highest_tier"] == tier_name]
        lines = []
        for row in tier_rows:
            status = "🟢 Active" if row["is_active"] else "⚪ Former A&T Driver"
            registry = (
                f" **#{int(row['immortal_number']):03d}** |"
                if row["immortal_number"] is not None else ""
            )
            since = row[f"{tier_name.lower()}_since"]
            since_text = discord.utils.format_dt(since, style="D") if since else "Recorded"
            line = (
                f"{registry} <@{row['discord_user_id']}> — "
                f"**{int(row['highest_verified_real_miles']):,}** Real Miles\n"
                f"`{row['trucksbook_name']}` • {status} • Since {since_text}"
            )
            if sum(len(item) + 1 for item in lines) + len(line) > remaining_budget:
                lines.append("*Further permanent records are safely stored in PostgreSQL.*")
                break
            lines.append(line)
        description = "\n\n".join(lines) if lines else "*No drivers recorded in this tier yet.*"
        remaining_budget -= len(description)
        embeds.append(discord.Embed(
            title=f"{icons[tier_name]} A&T {tier_name.upper()}S",
            description=description,
            colour=colours[tier_name],
        ))
    embeds[0].description = (
        "**A&T Transport LTD — Permanent Hall of Fame**\n"
        "Earned with verified Real Miles. Membership history is never erased.\n\n"
        + embeds[0].description
    )
    embeds[-1].set_footer(
        text="Veteran 250K • Elite 500K • Immortal 1M • Active/Former shown separately"
    )
    return await upsert_controlled_message(
        "hall_of_fame", HALL_OF_FAME_CHANNEL_ID, embeds
    )


async def refresh_40k_reward_hub():
    if MILEAGE_CLUB_40K_CHANNEL_ID is None or db_pool is None:
        return False
    async with db_pool.acquire() as connection:
        count = await connection.fetchval(
            "SELECT COUNT(*) FROM permanent_achievements WHERE achievement_miles = 40000;"
        )
    embed = discord.Embed(
        title="🎁 A&T 40K MILEAGE CLUB REWARD HUB",
        description=(
            "Welcome to the reward hub for drivers who have earned "
            "**40,000 verified A&T Real Miles**.\n\n"
            "Your 40K achievement is permanent and remains recorded even as your "
            "normal progression rank continues to change."
        ),
        colour=discord.Colour.from_rgb(31, 150, 135),
    )
    embed.add_field(name="Permanent 40K Members", value=f"**{int(count):,}**")
    embed.set_footer(text="A&T Transport LTD • Driven Beyond Horizons")
    return await upsert_controlled_message(
        "40k_reward_hub", MILEAGE_CLUB_40K_CHANNEL_ID, [embed]
    )


async def sync_permanent_system_for_driver(
    guild, discord_user_id, trucksbook_name, real_miles, announce=False,
    refresh_displays=True,
):
    member = guild.get_member(discord_user_id) if guild else None
    result = await reconcile_permanent_records(
        discord_user_id,
        trucksbook_name,
        int(real_miles),
        member is not None,
    )
    if guild and member:
        await sync_member_permanent_roles(guild, discord_user_id, int(real_miles))
    if announce:
        for achievement in result["new_achievements"]:
            await announce_permanent_achievement(
                guild, member, discord_user_id, trucksbook_name,
                achievement, int(real_miles)
            )
    if refresh_displays and result["hall_changed"]:
        await refresh_hall_of_fame_display()
    if refresh_displays and any(
        item["miles"] == 40000 for item in result["new_achievements"]
    ):
        await refresh_40k_reward_hub()
    return result


async def silent_historical_permanent_sync(guild):
    """Backfill history and roles without announcing old threshold crossings."""
    global permanent_history_synced
    if permanent_history_synced or db_pool is None:
        return
    async with PERMANENT_SYNC_LOCK:
        if permanent_history_synced:
            return
        async with db_pool.acquire() as connection:
            rows = await connection.fetch(
                "SELECT discord_user_id, trucksbook_name, real_miles FROM driver_progress;"
            )
        synced = 0
        failures = 0
        for row in rows:
            try:
                await sync_permanent_system_for_driver(
                    guild,
                    int(row["discord_user_id"]),
                    row["trucksbook_name"],
                    int(row["real_miles"]),
                    announce=False,
                    refresh_displays=False,
                )
                synced += 1
            except (discord.Forbidden, discord.HTTPException) as error:
                failures += 1
                print(f"PERMANENT HISTORICAL SYNC ROLE ERROR: {error}")
            except Exception as error:
                failures += 1
                print(f"PERMANENT HISTORICAL SYNC ERROR: {error}")
        await refresh_hall_of_fame_display()
        await refresh_40k_reward_hub()
        permanent_history_synced = True
        print(
            f"Permanent historical sync complete: {synced} driver(s), "
            f"{failures} failure(s), no historical announcements."
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

        if db_pool is not None:
            await silent_historical_permanent_sync(guild)
            try:
                await maintain_driver_leaderboard()
            except Exception as error:
                print(f"LEADERBOARD STARTUP ERROR: {error}")

            if not leaderboard_rollover_task.is_running():
                leaderboard_rollover_task.start()

        check_onboarding_configuration(
            guild
        )

        try:
            await sync_all_role_category_dividers(guild)
        except Exception as error:
            print(f"ROLE DIVIDER STARTUP SYNC ERROR: {error}")

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


@bot.event
async def on_member_join(member):
    """Restore earned permanent roles and Active status when a driver returns."""
    try:
        await sync_member_role_category_dividers(member)
    except (discord.Forbidden, discord.HTTPException) as error:
        print(f"ROLE DIVIDER MEMBER-JOIN SYNC ERROR: {member.id}: {error}")

    if member.guild.id != GUILD_ID or db_pool is None:
        return
    async with db_pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT trucksbook_name, real_miles
            FROM driver_progress
            WHERE discord_user_id = $1;
            """,
            member.id,
        )
    if row is None:
        return
    try:
        await sync_permanent_system_for_driver(
            member.guild,
            member.id,
            row["trucksbook_name"],
            int(row["real_miles"]),
            announce=False,
        )
        await sync_member_progression_role(
            member.guild, member.id, int(row["real_miles"])
        )
    except Exception as error:
        print(f"RETURNING DRIVER SYNC ERROR: {member.id}: {error}")
    try:
        await refresh_driver_leaderboard(member.guild)
    except Exception as error:
        print(f"LEADERBOARD MEMBER-JOIN REFRESH ERROR: {error}")

@bot.event
async def on_member_update(before, after):
    """Keep decorative divider roles aligned with functional roles."""
    before_role_ids = {role.id for role in before.roles}
    after_role_ids = {role.id for role in after.roles}
    booster_changed = before.premium_since != after.premium_since

    if before_role_ids == after_role_ids and not booster_changed:
        return

    try:
        await sync_member_role_category_dividers(after)
    except (discord.Forbidden, discord.HTTPException) as error:
        print(f"ROLE DIVIDER MEMBER-UPDATE ERROR: {after.id}: {error}")


@bot.event
async def on_member_remove(member):
    """Retain Hall of Fame history and show the member as a former driver."""
    if member.guild.id != GUILD_ID or db_pool is None:
        return
    async with db_pool.acquire() as connection:
        result = await connection.execute(
            """
            UPDATE hall_of_fame
            SET is_active = FALSE, updated_at = NOW()
            WHERE discord_user_id = $1 AND is_active = TRUE;
            """,
            member.id,
        )
    if result != "UPDATE 0":
        await refresh_hall_of_fame_display()
    try:
        await refresh_driver_leaderboard(member.guild)
    except Exception as error:
        print(f"LEADERBOARD MEMBER-REMOVE REFRESH ERROR: {error}")


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

            # Permanent achievements and Hall of Fame are intentionally
            # independent from the highest-current-only progression role.
            try:
                await sync_permanent_system_for_driver(
                    guild,
                    discord_user_id,
                    trucksbook_name,
                    real_miles,
                    announce=True,
                )
            except Exception as error:
                print(
                    "PERMANENT ACHIEVEMENT ERROR: Mileage remains saved; "
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

            try:
                await refresh_driver_leaderboard(guild)
            except Exception as error:
                print(
                    "LEADERBOARD REFRESH ERROR: Mileage remains saved; "
                    f"{error}"
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
                progress_row = await connection.fetchrow(
                    "SELECT trucksbook_name, real_miles FROM driver_progress "
                    "WHERE discord_user_id = $1;",
                    user_id,
                )
            await sync_member_progression_role(
                ctx.guild, user_id, int(progress_row["real_miles"])
            )
            # Repairs restore historical mileage, so backfill permanent records
            # and roles silently just like startup history migration.
            await sync_permanent_system_for_driver(
                ctx.guild,
                user_id,
                progress_row["trucksbook_name"],
                int(progress_row["real_miles"]),
                announce=False,
            )
        except Exception as error:
            role_failures += 1
            print(f"REPAIR ROLE SYNC ERROR for {user_id}: {error}")

    achievements_sent, achievement_failures = (
        await announce_pending_repair_achievements(ctx.guild)
    )

    try:
        await refresh_driver_leaderboard(ctx.guild)
    except Exception as error:
        print(f"LEADERBOARD REPAIR REFRESH ERROR: {error}")

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

def build_driver_profile_embed(target_member, profile):
    """Build the original read-only embed used as the safe fallback."""
    trucksbook_name = profile["trucksbook_name"]
    real_miles = profile["real_miles"]
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
    embed.add_field(
        name="Verified Real Jobs",
        value=f"**{profile['jobs_completed']:,}**",
        inline=True,
    )
    embed.add_field(
        name="Average Job",
        value=f"**{profile['average_job_distance']:,} mi**",
        inline=True,
    )
    embed.add_field(
        name="Longest Job",
        value=f"**{profile['longest_job_distance']:,} mi**",
        inline=True,
    )

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
    achievement_names = [
        name for threshold, _, name in ACHIEVEMENT_ROLES
        if threshold in profile["permanent_achievements"]
    ]
    embed.add_field(
        name="Permanent Mileage Achievements",
        value=(" • ".join(achievement_names) if achievement_names else "None earned yet"),
        inline=False,
    )
    hall_value = profile["hall_of_fame_tier"] or "Not yet inducted"
    if profile["immortal_number"] is not None:
        hall_value += f" • Immortal #{profile['immortal_number']:03d}"
    embed.add_field(name="Hall of Fame", value=f"**{hall_value}**", inline=True)
    embed.add_field(
        name="Competition Record",
        value=(
            f"Weekly Champion: **{profile['weekly_wins']}**\n"
            f"Driver of the Month: **{profile['monthly_wins']}**"
        ),
        inline=True,
    )
    embed.set_footer(text="A&T Transport LTD • Verified Real data • Driven Beyond Horizons")
    return embed


def profile_font_candidates(bold=False):
    """Return local-only fonts in broad Unicode coverage order."""
    return (
        (
            str(PROFILE_FONT_BOLD_PATH),
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
            "/usr/share/fonts/opentype/noto/NotoSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            "C:/Windows/Fonts/seguisb.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
        )
        if bold
        else (
            str(PROFILE_FONT_REGULAR_PATH),
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/opentype/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arial.ttf",
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


def fit_profile_text(draw, text, max_width, start_size, bold=False, min_size=11):
    """Choose a font size that keeps text inside its allocated safe width."""
    minimum = max(int(min_size), 8)
    for size in range(start_size, minimum - 1, -2):
        font = load_profile_font(size, bold=bold)
        box = draw.textbbox((0, 0), str(text), font=font)
        if box[2] - box[0] <= max_width:
            return font
    return load_profile_font(minimum, bold=bold)


def fit_profile_label(draw, text, max_width, start_size, bold=False, min_size=11):
    """Fit a label, adding an ellipsis only when the minimum size still cannot fit."""
    value = str(text)
    font = fit_profile_text(
        draw,
        value,
        max_width,
        start_size,
        bold=bold,
        min_size=min_size,
    )
    if draw.textbbox((0, 0), value, font=font)[2] <= max_width:
        return value, font
    ellipsis = "..."
    while value and draw.textbbox((0, 0), value + ellipsis, font=font)[2] > max_width:
        value = value[:-1].rstrip()
    return (value + ellipsis if value else ellipsis), font


def sanitise_profile_name(value, fallback="A&T DRIVER"):
    """Return a Unicode-safe, display-only name for the profile artwork.

    Compatibility alphabets are folded into ordinary text, invisible controls
    and emoji are discarded, and only conservative punctuation is retained.
    This avoids Linux font tofu without changing the stored Discord or
    TrucksBook value.
    """
    normalised = unicodedata.normalize("NFKC", str(value or ""))
    readable = []
    for character in normalised:
        category = unicodedata.category(character)
        if category[0] in {"L", "M", "N"}:
            readable.append(character)
        elif character.isspace():
            readable.append(" ")
        elif character in " ._-'&+@#()":
            readable.append(character)
    cleaned = " ".join("".join(readable).split()).strip(" ._-'&+@#()")
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


def profile_composite(base, overlay, position=(0, 0)):
    """Composite one RGBA layer while preserving the active Pillow canvas."""
    base.alpha_composite(overlay, dest=position)


class ProfileCanvasDraw:
    """Use a fresh ImageDraw handle per operation around RGBA composites.

    Pillow can invalidate a long-lived drawing handle after an in-place image
    composite.  Reacquiring it prevents intermittent missing or clipped text
    and badges across prestige tiers while keeping the renderer deterministic.
    """

    def __init__(self, image):
        self.image = image

    def __getattr__(self, method_name):
        def draw_operation(*args, **kwargs):
            operation = getattr(ImageDraw.Draw(self.image, "RGBA"), method_name)
            return operation(*args, **kwargs)
        return draw_operation


def profile_panel(draw, box, radius=24, outline=(83, 184, 212, 115)):
    """Paint one translucent, blue-steel panel."""
    draw.rounded_rectangle(
        box,
        radius=radius,
        fill=(5, 16, 29, 168),
        outline=outline,
        width=2,
    )


def profile_prestige_theme(real_miles, hall_of_fame_tier=None):
    """Return one of the seven whole-profile prestige states."""
    if real_miles >= 1000000:
        return {"name": "A&T IMMORTAL", "index": 6, "recognition": "1,000,000+ VERIFIED REAL MILES", "accent": (250, 207, 91, 255), "secondary": (91, 242, 231, 255), "glow": (231, 172, 51, 118), "frame": (250, 213, 116, 245), "aurora": (44, 236, 211, 70), "runes": 3}
    if real_miles >= 750000:
        return {"name": "HORIZON ELITE", "index": 5, "recognition": "THE FINAL HORIZON AWAITS.", "accent": (241, 195, 83, 255), "secondary": (78, 228, 244, 255), "glow": (28, 205, 224, 105), "frame": (225, 190, 100, 230), "aurora": (42, 218, 238, 62), "runes": 3}
    if real_miles >= 500000:
        return {"name": "A&T ELITE", "index": 4, "recognition": "HALF-MILLION CLUB", "accent": (238, 190, 78, 255), "secondary": (87, 225, 239, 255), "glow": (38, 203, 204, 86), "frame": (220, 181, 91, 220), "aurora": (35, 200, 213, 52), "runes": 2}
    if real_miles >= 250000:
        return {"name": "A&T VETERAN", "index": 3, "recognition": "QUARTER MILLION VETERAN", "accent": (215, 181, 105, 255), "secondary": (92, 218, 236, 255), "glow": (37, 177, 205, 70), "frame": (137, 207, 217, 215), "aurora": (32, 181, 208, 45), "runes": 2}
    if real_miles >= 100000:
        return {"name": "A&T CENTURION", "index": 2, "recognition": "100K+ VERIFIED REAL MILES", "accent": (232, 188, 86, 255), "secondary": (83, 216, 236, 255), "glow": (37, 177, 205, 60), "frame": (91, 194, 216, 210), "aurora": (31, 173, 205, 40), "runes": 2}
    if real_miles >= 40000:
        return {"name": "40K MILEAGE CLUB", "index": 1, "recognition": "40K MILEAGE CLUB", "accent": (226, 184, 91, 255), "secondary": (79, 215, 235, 255), "glow": (34, 173, 204, 52), "frame": (184, 165, 104, 205), "aurora": (28, 166, 200, 36), "runes": 1}
    return {"name": "A&T VERIFIED DRIVER", "index": 0, "recognition": "A&T VERIFIED DRIVER", "accent": (181, 205, 214, 255), "secondary": (80, 212, 235, 255), "glow": (32, 160, 194, 40), "frame": (67, 177, 214, 195), "aurora": (24, 151, 188, 30), "runes": 1}


PROFILE_PREVIEW_STATES = (
    ("01-standard", 20000, None, None),
    ("02-40k-mileage-club", 57096, None, None),
    ("03-centurion", 125000, None, None),
    ("04-veteran", 325000, "Veteran", None),
    ("05-elite", 625000, "Elite", None),
    ("06-horizon-elite", 825000, "Elite", None),
    ("07-immortal", 1050000, "Immortal", 1),
)


def load_profile_atlas_cell(path, columns, rows, index, maximum_size):
    """Load one transparent cell from a committed, local-only artwork atlas."""
    if not path.is_file():
        raise FileNotFoundError(f"Profile artwork not found: {path}")
    with Image.open(path) as source:
        source = source.convert("RGBA")
        cell_width = source.width // columns
        cell_height = source.height // rows
        column, row = index % columns, index // columns
        cell = source.crop((column * cell_width, row * cell_height, (column + 1) * cell_width, (row + 1) * cell_height))
        alpha_box = cell.getchannel("A").getbbox()
        if alpha_box:
            cell = cell.crop(alpha_box)
        cell.thumbnail(maximum_size, Image.Resampling.LANCZOS)
        return cell


def load_profile_icon(name, maximum_size):
    icon_order = {"verified": 0, "path": 1, "real_miles": 2, "career": 3, "hall": 4, "achievement": 5, "champion": 6, "brand": 7}
    return load_profile_atlas_cell(PROFILE_ICON_ATLAS_PATH, 4, 2, icon_order[name], maximum_size)


def load_prestige_insignia(index, maximum_size):
    return load_profile_atlas_cell(PROFILE_PRESTIGE_ATLAS_PATH, 7, 1, index, maximum_size)


def build_profile_backdrop(width, height):
    """Load permanent profile art, with a self-contained cinematic fallback."""
    if PROFILE_TEMPLATE_PATH.is_file():
        with Image.open(PROFILE_TEMPLATE_PATH) as template_source:
            return ImageOps.fit(
                template_source.convert("RGBA"),
                (width, height),
                method=Image.Resampling.LANCZOS,
            )

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


def render_driver_profile_v5_png(avatar_bytes, discord_name, profile):
    """Render the final cinematic profile from read-only driver values."""
    if Image is None:
        raise RuntimeError("Pillow is not installed")

    trucksbook_name = profile["trucksbook_name"]
    real_miles = profile["real_miles"]
    current_miles, _, current_rank_name = get_progression_role(real_miles)
    next_role = get_next_progression_role(real_miles)
    prestige = profile_prestige_theme(
        real_miles,
        profile.get("hall_of_fame_tier"),
    )
    updated_at = datetime.now(UK_TIMEZONE)
    updated_label = updated_at.strftime("%d %b %Y • %H:%M %Z").upper()
    width, height = 1600, 1200
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
    profile_composite(card, vignette)
    prestige_wash = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    prestige_draw = ImageDraw.Draw(prestige_wash, "RGBA")
    prestige_draw.ellipse(
        (280, -270, 1450, 720),
        fill=prestige["glow"],
    )
    prestige_draw.ellipse((90, -220, 1510, 610), fill=prestige["aurora"])
    if real_miles >= 500000:
        prestige_draw.arc(
            (1110, -110, 1690, 470),
            90,
            278,
            fill=(*prestige["accent"][:3], 115),
            width=8,
        )
    profile_composite(card, prestige_wash.filter(ImageFilter.GaussianBlur(70)))
    treatment_level = prestige["index"]

    # Prestige atmosphere is deliberately painted before every information
    # panel.  This preserves the frozen geometry and readability while making
    # the seven states recognisable from their silhouette and sky treatment.
    atmosphere = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    atmosphere_draw = ImageDraw.Draw(atmosphere, "RGBA")
    if treatment_level == 0:
        atmosphere_draw.arc((190, -150, 1410, 560), 198, 342, fill=(153, 202, 216, 38), width=3)
    elif treatment_level == 2:
        for inset in (0, 22, 44):
            atmosphere_draw.arc((300 + inset, -160 + inset, 1300 - inset, 510), 205, 335, fill=(232, 188, 86, 31), width=3)
    elif treatment_level == 3:
        atmosphere_draw.rectangle((0, 0, width, height), fill=(0, 5, 13, 24))
        for x in (82, 1518):
            for y in range(210, 990, 112):
                atmosphere_draw.polygon(((x, y - 13), (x + 9, y), (x, y + 13), (x - 9, y)), outline=(215, 181, 105, 82))
    elif treatment_level == 4:
        atmosphere_draw.arc((-80, 80, 1680, 970), 205, 335, fill=(238, 190, 78, 74), width=7)
        atmosphere_draw.line((250, 620, 1350, 620), fill=(238, 190, 78, 42), width=3)
    elif treatment_level >= 5:
        star_positions = ((126, 178), (246, 86), (388, 230), (520, 72), (1044, 180), (1190, 74), (1348, 218), (1480, 104))
        for star_x, star_y in star_positions:
            radius = 3 if treatment_level == 5 else 5
            atmosphere_draw.line((star_x - radius * 2, star_y, star_x + radius * 2, star_y), fill=(*prestige["secondary"][:3], 115), width=1)
            atmosphere_draw.line((star_x, star_y - radius * 2, star_x, star_y + radius * 2), fill=(*prestige["accent"][:3], 125), width=1)
        atmosphere_draw.arc((-130, -260, 1730, 920), 198, 342, fill=(*prestige["accent"][:3], 100), width=7 if treatment_level == 5 else 10)
        if treatment_level == 6:
            atmosphere_draw.arc((-70, -205, 1670, 865), 201, 339, fill=(*prestige["secondary"][:3], 116), width=5)
            atmosphere_draw.ellipse((610, -195, 990, 185), outline=(250, 225, 144, 80), width=5)
    profile_composite(card, atmosphere)
    draw = ProfileCanvasDraw(card)

    white = (238, 246, 249, 255)
    ice = (91, 218, 242, 255)
    muted = (157, 186, 199, 255)
    gold = prestige["accent"]
    teal = prestige["secondary"]

    draw.rounded_rectangle((14, 14, 1586, 1186), 28, outline=prestige["frame"], width=3)
    draw.rounded_rectangle((22, 22, 1578, 1178), 23, outline=(*gold[:3], 125), width=1)
    for rune_index in range(prestige["runes"]):
        inset = 31 + rune_index * 7
        draw.line((inset, 165, inset, 1035), fill=(*teal[:3], 70 + rune_index * 15), width=1)
        draw.line((1600 - inset, 165, 1600 - inset, 1035), fill=(*gold[:3], 65 + rune_index * 15), width=1)
    # Each prestige tier changes the whole card, not only its label. The
    # progressively denser corner rails and Nordic diamonds are deliberately
    # restrained so the shared A&T composition remains intact.
    if treatment_level:
        for ornament in range(1, treatment_level + 1):
            y = 188 + ornament * 78
            size = 7 + ornament
            for x, colour in ((36 + ornament * 7, teal), (1564 - ornament * 7, gold)):
                draw.polygon(
                    ((x, y - size), (x + size, y), (x, y + size), (x - size, y)),
                    outline=(*colour[:3], 90 + ornament * 12),
                )
    if treatment_level >= 4:
        draw.arc((7, 6, 250, 249), 183, 276, fill=(*gold[:3], 185), width=4)
        draw.arc((1350, 6, 1593, 249), 264, 357, fill=(*teal[:3], 185), width=4)
    if treatment_level == 6:
        draw.rounded_rectangle((28, 28, 1572, 1172), 22, outline=(*teal[:3], 165), width=2)
    # Frosted glass is deliberately local, leaving the aurora and mountain
    # skyline visible across the poster instead of recreating a dashboard.
    draw.rounded_rectangle((43, 39, 947, 139), 20, fill=(2, 10, 20, 102), outline=(83, 195, 218, 95), width=1)
    draw.rounded_rectangle((43, 163, 358, 677), 24, fill=(2, 10, 20, 118), outline=(83, 195, 218, 100), width=1)
    draw.rounded_rectangle((376, 610, 947, 758), 24, fill=(2, 10, 20, 112), outline=(83, 195, 218, 100), width=1)
    draw.rounded_rectangle((43, 770, 947, 960), 22, fill=(2, 10, 20, 132), outline=(83, 195, 218, 105), width=1)
    draw.rounded_rectangle((974, 24, 1574, 1176), 24, fill=(1, 9, 18, 128), outline=(41, 142, 178, 145), width=2)

    brand_icon = load_profile_icon("brand", (58, 58))
    profile_composite(card, brand_icon, (64, 51))
    profile_text(draw, (132, 58), "A&T TRANSPORT LTD", load_profile_font(36, True), white)
    verified_icon = load_profile_icon("verified", (34, 34))
    profile_composite(card, verified_icon, (72, 96))
    profile_text(draw, (112, 103), "OFFICIAL VERIFIED DRIVER PROFILE - DRIVEN BEYOND HORIZONS", load_profile_font(14, True), ice, shadow=0)
    draw.line((73, 124, 918, 124), fill=(73, 205, 229, 185), width=2)

    # The complete transparent insignia is contained inside a dedicated header
    # safe area.  Keeping the label above the divider prevents every prestige
    # state (not only 40K) from being clipped or visually squeezed.
    prestige_centre_x = 829
    prestige_insignia = load_prestige_insignia(prestige["index"], (112, 64))
    prestige_insignia_x = prestige_centre_x - prestige_insignia.width // 2
    prestige_insignia_y = 43 + (64 - prestige_insignia.height) // 2
    profile_composite(card, prestige_insignia, (prestige_insignia_x, prestige_insignia_y))
    prestige_label, prestige_font = fit_profile_label(
        draw, prestige["name"], 208, 14, True, min_size=11
    )
    profile_text(draw, (prestige_centre_x, 106), prestige_label, prestige_font, gold, anchor="ma", shadow=0)

    with Image.open(BytesIO(avatar_bytes)) as avatar_source:
        avatar = rounded_profile_image(avatar_source, (152, 152), 76)
    avatar_glow = Image.new("RGBA", (172, 172), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(avatar_glow, "RGBA")
    glow_draw.ellipse((14, 14, 158, 158), outline=(57, 226, 238, 205), width=12)
    profile_composite(card, avatar_glow.filter(ImageFilter.GaussianBlur(10)), (114, 181))
    profile_composite(card, avatar, (124, 191))
    draw.ellipse((120, 187, 280, 347), outline=(121, 236, 242, 245), width=4)

    safe_discord_name = sanitise_profile_name(discord_name)
    safe_trucksbook_name = sanitise_profile_name(trucksbook_name, "UNLINKED DRIVER")
    safe_discord_name, discord_font = fit_profile_label(
        draw, safe_discord_name, 276, 66, True, min_size=18
    )
    safe_trucksbook_name, trucksbook_font = fit_profile_label(
        draw, safe_trucksbook_name, 270, 21, True, min_size=12
    )
    profile_text(draw, (200, 388), safe_discord_name, discord_font, white, anchor="mm", shadow=3)
    profile_text(draw, (200, 431), "DISCORD DRIVER", load_profile_font(13, True), ice, anchor="ma", shadow=0)
    draw.line((78, 455, 323, 455), fill=(91, 197, 214, 130), width=1)
    profile_text(draw, (200, 480), "TRUCKSBOOK DRIVER", load_profile_font(12, True), ice, anchor="ma", shadow=0)
    profile_text(draw, (200, 515), safe_trucksbook_name, trucksbook_font, muted, anchor="ma")
    draw.line((78, 542, 323, 542), fill=(91, 197, 214, 90), width=1)
    identity_verified = load_profile_icon("verified", (45, 45))
    profile_composite(card, identity_verified, (92, 554))
    profile_text(draw, (222, 578), "VERIFIED A&T DRIVER", load_profile_font(14, True), gold, anchor="ma", shadow=0)
    profile_text(draw, (200, 617), prestige["recognition"], fit_profile_text(draw, prestige["recognition"], 270, 12, True), teal, anchor="ma", shadow=0)

    current_badge = load_profile_badge(current_miles, (535, 478))
    hero_x = 659
    badge_x = hero_x - current_badge.width // 2
    badge_y = 164 + (286 - current_badge.height) // 2
    glow = Image.new("RGBA", (590, 465), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow, "RGBA")
    cyan_glow_alpha = {0: 92, 1: 145, 2: 150, 3: 126, 4: 158, 5: 178, 6: 195}[treatment_level]
    gold_glow_alpha = {0: 22, 1: 62, 2: 74, 3: 82, 4: 96, 5: 108, 6: 135}[treatment_level]
    glow_draw.ellipse((50, 15, 540, 450), fill=(24, 190, 221, cyan_glow_alpha))
    glow_draw.ellipse((115, 55, 475, 420), fill=(236, 184, 68, gold_glow_alpha))
    profile_composite(card, glow.filter(ImageFilter.GaussianBlur(40)), (364, 118))
    profile_composite(card, current_badge, (badge_x, badge_y))
    profile_text(draw, (hero_x, 160), "CURRENT A&T RANK", load_profile_font(15, True), ice, anchor="ma", shadow=0)
    # A local glass ribbon protects the two hero lines from bright Aurora
    # artwork at Discord/mobile scale without hiding the surrounding scenery.
    draw.rounded_rectangle(
        (390, 468, 928, 589),
        22,
        fill=(0, 7, 16, 178),
        outline=(*teal[:3], 128),
        width=1,
    )
    hero_rank_label, hero_rank_font = fit_profile_label(
        draw, current_rank_name.upper(), 510, 72, True, min_size=29
    )
    profile_text(draw, (hero_x, 505), hero_rank_label, hero_rank_font, white, anchor="mm", shadow=3)
    mileage_headline = f"{real_miles:,} VERIFIED REAL MILES"
    profile_text(
        draw,
        (hero_x, 559),
        mileage_headline,
        fit_profile_text(draw, mileage_headline, 510, 54, True, min_size=27),
        gold,
        anchor="mm",
        shadow=3,
    )

    if next_role is None:
        next_miles = PROGRESSION_ROLES[-1][0]
        next_name = "Path to Immortal Complete"
        miles_remaining = 0
        progress = 1.0
        progress_caption = f"{real_miles:,} VERIFIED REAL MILES  •  DRIVEN BEYOND HORIZONS"
    else:
        next_miles, _, next_name = next_role
        miles_remaining = max(next_miles - real_miles, 0)
        stage_span = max(next_miles - current_miles, 1)
        progress = min(max((real_miles - current_miles) / stage_span, 0.0), 1.0)
        progress_caption = f"{real_miles:,} / {next_miles:,} VERIFIED REAL MILES"

    if next_role is None:
        path_icon = load_profile_icon("path", (62, 62))
        profile_composite(card, path_icon, (394, 615))
        profile_text(draw, (659, 626), "PATH TO IMMORTAL COMPLETE", load_profile_font(23, True), gold, anchor="ma")
        profile_text(draw, (659, 658), "1,000,000+ VERIFIED REAL MILES", load_profile_font(14, True), white, anchor="ma", shadow=0)
    else:
        profile_text(draw, (407, 626), "NEXT RANK", load_profile_font(12, True), ice, shadow=0)
        next_rank_label, next_rank_font = fit_profile_label(
            draw, next_name.upper(), 320, 22, True, min_size=13
        )
        profile_text(draw, (407, 652), next_rank_label, next_rank_font, white)
        profile_text(draw, (916, 626), "MILES REMAINING", load_profile_font(12, True), ice, anchor="ra", shadow=0)
        remaining_label = f"{miles_remaining:,}"
        profile_text(draw, (916, 652), remaining_label, fit_profile_text(draw, remaining_label, 180, 26, True, min_size=18), gold, anchor="ra")

    bar_left, bar_top, bar_right, bar_bottom = 407, 687, 916, 713
    draw.rounded_rectangle((bar_left - 4, bar_top - 4, bar_right + 4, bar_bottom + 4), 16, fill=(31, 142, 171, 90))
    draw.rounded_rectangle((bar_left, bar_top, bar_right, bar_bottom), 13, fill=(8, 29, 43, 245), outline=(98, 197, 218, 220), width=2)
    filled_right = bar_left + int((bar_right - bar_left) * progress)
    if filled_right > bar_left:
        draw.rounded_rectangle((bar_left, bar_top, max(filled_right, bar_left + 27), bar_bottom), 13, fill=(48, 219, 223, 255))
        draw.line((bar_left + 12, bar_top + 5, max(filled_right - 10, bar_left + 14), bar_top + 5), fill=(215, 255, 251, 205), width=3)
    profile_text(draw, (bar_left, 739), progress_caption, load_profile_font(12, True), white, shadow=1)
    profile_text(
        draw,
        (bar_right, 739),
        "COMPLETE" if next_role is None else f"{progress * 100:.1f}% TO NEXT RANK",
        fit_profile_text(draw, "COMPLETE" if next_role is None else f"{progress * 100:.1f}% TO NEXT RANK", 245, 17, True, min_size=12),
        gold,
        anchor="ra",
    )

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
            profile_composite(card, earned_glow.filter(ImageFilter.GaussianBlur(18)), (centre_x - 80, 800))
        profile_composite(card,
            milestone_badge,
            (centre_x - milestone_badge.width // 2, 802 + (112 - milestone_badge.height) // 2),
        )
        label_colour = white if is_earned else muted
        threshold_colour = gold if is_earned else (104, 122, 132, 255)
        milestone_label, milestone_font = fit_profile_label(
            draw, role_names[threshold].upper(), 165, 13, True, min_size=10
        )
        profile_text(draw, (centre_x, 914), milestone_label, milestone_font, label_colour, anchor="ma", shadow=1)
        profile_text(draw, (centre_x, 938), f"{threshold:,} MI", load_profile_font(12, True), threshold_colour, anchor="ma", shadow=0)

    path_header_icon = load_profile_icon("path", (45, 45))
    profile_composite(card, path_header_icon, (1010, 37))
    profile_text(draw, (1290, 48), "PATH TO IMMORTAL", load_profile_font(27, True), white, anchor="ma")
    profile_text(draw, (1274, 84), "0 — 1,000,000 REAL MILES", load_profile_font(15, True), gold, anchor="ma", shadow=1)
    draw.line((1006, 114, 1541, 114), fill=(76, 195, 219, 190), width=2)
    for index, (threshold, _, rank_name) in enumerate(PROGRESSION_ROLES):
        column, row = index % 5, index // 5
        left, top = 992 + column * 111, 128 + row * 157
        centre_x = left + 50
        is_current = threshold == current_miles
        is_earned = threshold <= real_miles
        if is_current:
            current_tile_glow = Image.new("RGBA", (122, 164), (0, 0, 0, 0))
            current_tile_draw = ImageDraw.Draw(current_tile_glow, "RGBA")
            current_tile_draw.rounded_rectangle(
                (8, 8, 114, 156), 16, outline=(*teal[:3], 185), width=7
            )
            profile_composite(
                card,
                current_tile_glow.filter(ImageFilter.GaussianBlur(8)),
                (left - 11, top - 12),
            )
            draw.rounded_rectangle((left - 3, top - 4, left + 103, top + 145), 13, fill=(15, 94, 121, 105), outline=(93, 231, 239, 240), width=2)
        elif is_earned:
            draw.rounded_rectangle((left - 2, top - 3, left + 102, top + 144), 13, fill=(8, 51, 64, 45), outline=(56, 177, 183, 90), width=1)
        ladder_badge = load_profile_badge(threshold, (99, 112))
        if not is_earned:
            greyscale = ImageOps.grayscale(ladder_badge).convert("RGBA")
            greyscale.putalpha(ladder_badge.getchannel("A").point(lambda alpha: int(alpha * 0.46)))
            ladder_badge = greyscale
        profile_composite(card, ladder_badge, (centre_x - ladder_badge.width // 2, top + (112 - ladder_badge.height) // 2))
        if is_current:
            draw.rounded_rectangle(
                (centre_x - 43, top + 4, centre_x + 43, top + 26),
                7,
                fill=(4, 29, 40, 225),
                outline=(*gold[:3], 235),
                width=1,
            )
            profile_text(
                draw,
                (centre_x, top + 15),
                "PATH COMPLETE" if treatment_level == 6 else "YOU ARE HERE",
                load_profile_font(9, True),
                gold,
                anchor="mm",
                shadow=0,
            )
        threshold_colour = gold if is_current else (teal if is_earned else muted)
        draw.rounded_rectangle((centre_x - 43, top + 116, centre_x + 43, top + 140), 6, fill=(4, 21, 34, 178), outline=(49, 139, 163, 145), width=1)
        profile_text(draw, (centre_x, top + 128), f"{threshold:,}", load_profile_font(12, True), threshold_colour, anchor="mm", shadow=1)

    # Permanent records live beneath the complete badge ladder. Convoy UI is
    # deliberately absent until that project has a production data source.
    draw.line((1014, 904, 1535, 904), fill=(73, 180, 207, 120), width=1)
    achievement_icon = load_profile_icon("achievement", (28, 28))
    profile_composite(card, achievement_icon, (1014, 910))
    profile_text(draw, (1050, 916), "PERMANENT MILEAGE ACHIEVEMENTS", load_profile_font(13, True), white)
    earned_achievements = set(profile["permanent_achievements"])
    for index, (threshold, _, _) in enumerate(ACHIEVEMENT_ROLES):
        short_label = "1M" if threshold == 1000000 else f"{threshold // 1000}K"
        is_earned = threshold in earned_achievements
        centre_x = 1048 + index * 91
        medallion = load_profile_icon("achievement", (42, 36))
        if not is_earned:
            medallion = ImageOps.grayscale(medallion).convert("RGBA")
            medallion.putalpha(medallion.getchannel("A").point(lambda alpha: int(alpha * 0.40)))
        elif index:
            # Higher permanent milestones retain the same local A&T medal
            # family while gaining increasingly elaborate rings and points.
            evolved_medallion = Image.new("RGBA", (54, 42), (0, 0, 0, 0))
            evolved_draw = ImageDraw.Draw(evolved_medallion, "RGBA")
            evolved_draw.ellipse((8 - index // 2, 2, 46 + index // 2, 40), outline=(*gold[:3], 150 + index * 16), width=1 + index // 2)
            if index >= 2:
                evolved_draw.polygon(((27, 0), (31, 6), (27, 10), (23, 6)), fill=(*teal[:3], 205))
            if index >= 4:
                evolved_draw.line((3, 21, 51, 21), fill=(*gold[:3], 145), width=2)
            evolved_medallion.alpha_composite(medallion, ((54 - medallion.width) // 2, (42 - medallion.height) // 2))
            medallion = evolved_medallion
        profile_composite(card, medallion, (centre_x - medallion.width // 2, 940))
        draw.rounded_rectangle((centre_x - 30, 981, centre_x + 30, 1005), 8, fill=(4, 18, 28, 220), outline=(*gold[:3], 210) if is_earned else (85, 102, 112, 120), width=1)
        profile_text(draw, (centre_x, 993), short_label, load_profile_font(12, True), gold if is_earned else muted, anchor="mm", shadow=1)

    hall_tier = (profile["hall_of_fame_tier"] or "").casefold()
    if hall_tier == "immortal" or profile["immortal_number"] is not None:
        hall_text = "A&T HALL OF FAME - IMMORTAL"
        registry_prefix = "SAMPLE IMMORTAL REGISTRY" if profile.get("is_preview") else "IMMORTAL REGISTRY"
        if profile["immortal_number"] is None:
            hall_subtext = "PERMANENT IMMORTAL REGISTRY"
        else:
            hall_subtext = f"{registry_prefix} #{profile['immortal_number']:03d}"
    elif hall_tier == "elite":
        hall_text = "A&T HALL OF FAME - ELITE"
        hall_subtext = "HORIZON ELITE HONOURS" if treatment_level == 5 else "HALF-MILLION LEGEND"
    elif hall_tier == "veteran":
        hall_text, hall_subtext = "A&T HALL OF FAME - VETERAN", "QUARTER-MILLION INDUCTEE"
    else:
        hall_text, hall_subtext = "A&T HALL OF FAME", "NOT YET INDUCTED"
    is_hall_member = hall_tier in {"veteran", "elite", "immortal"} or profile["immortal_number"] is not None
    draw.rounded_rectangle((1011, 1010, 1537, 1096), 18, fill=(20, 39, 43, 205) if is_hall_member else (5, 19, 31, 185), outline=(*gold[:3], 220) if is_hall_member else (74, 142, 162, 125), width=2)
    hall_icon = load_profile_icon("hall", (68, 68))
    if not is_hall_member:
        hall_icon = ImageOps.grayscale(hall_icon).convert("RGBA")
        hall_icon.putalpha(hall_icon.getchannel("A").point(lambda alpha: int(alpha * 0.42)))
    profile_composite(card, hall_icon, (1022, 1018))
    profile_text(draw, (1100, 1042), hall_text, fit_profile_text(draw, hall_text, 415, 23, True), gold if is_hall_member else white)
    profile_text(draw, (1100, 1073), hall_subtext, fit_profile_text(draw, hall_subtext, 415, 13, True), teal if is_hall_member else muted, shadow=0)
    profile_text(draw, (1274, 1117), "DRIVEN BEYOND HORIZONS", load_profile_font(13, True), ice, anchor="ma", shadow=0)
    profile_text(draw, (1274, 1137), "TOGETHER WE DRIVE  •  TOGETHER WE CONQUER", load_profile_font(10, True), muted, anchor="ma", shadow=0)
    profile_text(draw, (1274, 1152), f"LIVE A&T DRIVER RECORD - UPDATED {updated_label}", load_profile_font(10, True), muted, anchor="ma", shadow=0)

    # Career statistics are a single dynamic overlay over the permanent art.
    draw.rounded_rectangle((43, 980, 947, 1170), 22, fill=(2, 10, 20, 152), outline=(83, 195, 218, 120), width=1)
    career_icon = load_profile_icon("career", (42, 42))
    profile_composite(card, career_icon, (65, 987))
    profile_text(draw, (114, 1003), "VERIFIED CAREER RECORD", load_profile_font(19, True), white)
    stat_items = (
        ("REAL JOBS", f"{profile['jobs_completed']:,}", ""),
        ("AVERAGE JOB", f"{profile['average_job_distance']:,} MI", ""),
        ("LONGEST JOB", f"{profile['longest_job_distance']:,} MI", "PERSONAL BEST"),
        ("WEEKLY WINS", f"{profile['weekly_wins']:,}", "WEEKLY CHAMPION"),
        ("DRIVER OF MONTH", f"{profile['monthly_wins']:,}", "MONTHLY WINNER"),
    )
    for index, (label, value, ribbon) in enumerate(stat_items):
        centre_x = 130 + index * 178
        draw.rounded_rectangle((centre_x - 78, 1033, centre_x + 78, 1130), 15, fill=(5, 24, 38, 205), outline=(62, 166, 190, 125), width=1)
        if index >= 3:
            champion_icon = load_profile_icon("champion", (36, 36))
            profile_composite(card, champion_icon, (centre_x - 70, 1043))
        profile_text(draw, (centre_x, 1063), value, fit_profile_text(draw, value, 128 if index >= 3 else 145, 28, True), gold, anchor="ma")
        profile_text(draw, (centre_x, 1094), label, fit_profile_text(draw, label, 142, 10, True), ice, anchor="ma", shadow=0)
        if ribbon:
            profile_text(draw, (centre_x, 1115), ribbon, fit_profile_text(draw, ribbon, 140, 9, True), muted, anchor="ma", shadow=0)
    profile_text(draw, (69, 1152), "VERIFIED TRUCKSBOOK REAL JOBS ONLY", load_profile_font(11, True), muted, shadow=0)

    if profile.get("is_preview"):
        draw.rounded_rectangle((504, 1162, 1096, 1192), 10, fill=(1, 8, 17, 225), outline=(*gold[:3], 205), width=1)
        profile_text(
            draw,
            (800, 1177),
            "SYNTHETIC PREVIEW - NO PRODUCTION DATA USED",
            load_profile_font(13, True),
            gold,
            anchor="mm",
            shadow=0,
        )

    output = BytesIO()
    card.convert("RGB").save(output, format="PNG", optimize=True, compress_level=7)
    output.seek(0)
    return output


def generate_profile_prestige_previews(output_directory):
    """Render native and mobile synthetic previews without production reads."""
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)

    avatar = Image.new("RGBA", (512, 512), (3, 14, 29, 255))
    avatar_draw = ImageDraw.Draw(avatar, "RGBA")
    avatar_draw.ellipse((28, 28, 484, 484), fill=(5, 39, 63, 255), outline=(77, 226, 239, 255), width=14)
    avatar_draw.ellipse((90, 90, 422, 422), outline=(232, 190, 82, 220), width=8)
    avatar_draw.polygon(((256, 102), (340, 256), (256, 410), (172, 256)), fill=(24, 174, 202, 180), outline=(244, 207, 103, 255))
    profile_text(avatar_draw, (256, 256), "A&T", load_profile_font(94, True), (244, 248, 249, 255), anchor="mm", shadow=4)
    avatar_bytes = BytesIO()
    avatar.save(avatar_bytes, format="PNG")
    synthetic_achievement_thresholds = [threshold for threshold, _, _ in ACHIEVEMENT_ROLES]

    generated_paths = []
    preview_cases = list(PROFILE_PREVIEW_STATES) + [
        ("08-long-rank-and-names", 800000, "Elite", None),
        ("09-999999-miles", 999999, "Elite", None),
        ("10-1000000-plus", 1234567, "Immortal", 12345),
    ]
    for slug, real_miles, hall_tier, immortal_number in preview_cases:
        stress_case = slug.startswith(("08-", "09-", "10-"))
        profile = {
            "trucksbook_name": (
                "Extremely Long TrucksBook Driver Display Name"
                if stress_case else "Synthetic Test Driver"
            ),
            "real_miles": real_miles,
            "jobs_completed": max(real_miles // 800, 1),
            "average_job_distance": 800,
            "longest_job_distance": 2189,
            "permanent_achievements": [
                threshold for threshold in synthetic_achievement_thresholds
                if threshold <= real_miles
            ],
            "hall_of_fame_tier": hall_tier,
            "immortal_number": immortal_number,
            "weekly_wins": 27 if stress_case else min(real_miles // 100000, 9),
            "monthly_wins": 14 if stress_case else min(real_miles // 250000, 4),
            "is_preview": True,
        }
        preview = render_driver_profile_v5_png(
            avatar_bytes.getvalue(),
            (
                "MYSTIC THE AURORA ROADMASTER OF THE NORTHERN HORIZON"
                if stress_case else "MYSTIC"
            ),
            profile,
        )
        destination = output_directory / f"at-driver-profile-{slug}.png"
        destination.write_bytes(preview.getvalue())
        generated_paths.append(destination)
        with Image.open(BytesIO(preview.getvalue())) as native_preview:
            mobile_preview = native_preview.resize((800, 600), Image.Resampling.LANCZOS)
            mobile_destination = output_directory / f"at-driver-profile-{slug}-mobile.png"
            mobile_preview.save(mobile_destination, format="PNG", optimize=True, compress_level=7)
        generated_paths.append(mobile_destination)
    return generated_paths


async def fetch_driver_profile_stats(connection, discord_user_id):
    """Read one driver's profile statistics without locking or mutating data."""
    row = await connection.fetchrow(
        """
        SELECT
            links.trucksbook_name,
            COALESCE(progress.real_miles, 0)::BIGINT AS real_miles,
            COALESCE(jobs.jobs_completed, 0)::BIGINT AS jobs_completed,
            COALESCE(jobs.average_job_distance, 0)::BIGINT
                AS average_job_distance,
            COALESCE(jobs.longest_job_distance, 0)::BIGINT
                AS longest_job_distance,
            COALESCE(achievements.earned_thresholds, ARRAY[]::BIGINT[])
                AS permanent_achievements,
            fame.highest_tier AS hall_of_fame_tier,
            fame.immortal_number,
            COALESCE(wins.weekly_wins, 0)::BIGINT AS weekly_wins,
            COALESCE(wins.monthly_wins, 0)::BIGINT AS monthly_wins
        FROM driver_links AS links
        LEFT JOIN driver_progress AS progress
            ON progress.discord_user_id = links.discord_user_id
        LEFT JOIN LATERAL (
            SELECT
                COUNT(*)::BIGINT AS jobs_completed,
                ROUND(AVG(accepted_distance))::BIGINT
                    AS average_job_distance,
                MAX(accepted_distance)::BIGINT AS longest_job_distance
            FROM processed_jobs
            WHERE discord_user_id = links.discord_user_id
              AND LOWER(statistics) = 'real'
              AND accepted_distance > 0
        ) AS jobs ON TRUE
        LEFT JOIN LATERAL (
            SELECT ARRAY_AGG(achievement_miles ORDER BY achievement_miles)
                AS earned_thresholds
            FROM permanent_achievements
            WHERE discord_user_id = links.discord_user_id
        ) AS achievements ON TRUE
        LEFT JOIN hall_of_fame AS fame
            ON fame.discord_user_id = links.discord_user_id
        LEFT JOIN LATERAL (
            SELECT
                COUNT(*) FILTER (WHERE period_type = 'weekly')::BIGINT
                    AS weekly_wins,
                COUNT(*) FILTER (WHERE period_type = 'monthly')::BIGINT
                    AS monthly_wins
            FROM competition_wins
            WHERE discord_user_id = links.discord_user_id
        ) AS wins ON TRUE
        WHERE links.discord_user_id = $1;
        """,
        discord_user_id,
    )
    if row is None:
        return None
    return {
        "trucksbook_name": str(row["trucksbook_name"]),
        "real_miles": max(int(row["real_miles"] or 0), 0),
        "jobs_completed": max(int(row["jobs_completed"] or 0), 0),
        "average_job_distance": max(
            int(row["average_job_distance"] or 0), 0
        ),
        "longest_job_distance": max(
            int(row["longest_job_distance"] or 0), 0
        ),
        "permanent_achievements": tuple(
            int(value) for value in (row["permanent_achievements"] or ())
        ),
        "hall_of_fame_tier": row["hall_of_fame_tier"],
        "immortal_number": (
            int(row["immortal_number"])
            if row["immortal_number"] is not None else None
        ),
        "weekly_wins": max(int(row["weekly_wins"] or 0), 0),
        "monthly_wins": max(int(row["monthly_wins"] or 0), 0),
    }


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
            async with connection.transaction(readonly=True):
                profile = await fetch_driver_profile_stats(
                    connection,
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

    fallback_embed = build_driver_profile_embed(
        target_member,
        profile,
    )

    try:
        avatar_bytes = await target_member.display_avatar.with_size(256).read()
        profile_png = await asyncio.to_thread(
            render_driver_profile_v5_png,
            avatar_bytes,
            target_member.display_name,
            profile,
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

    try:
        await sync_permanent_system_for_driver(
            ctx.guild,
            applicant_id,
            link_name,
            final_real_miles,
            announce=False,
        )
    except Exception as error:
        print(f"CONFIRM PERMANENT ROLE ERROR: {error}")

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
# START BOT / READ-ONLY ARTWORK PREVIEW
# --------------------------------------------------

if "--render-profile-previews" in sys.argv:
    argument_index = sys.argv.index("--render-profile-previews")
    preview_directory = (
        Path(sys.argv[argument_index + 1])
        if len(sys.argv) > argument_index + 1
        else Path(__file__).resolve().parent / "profile-previews"
    )
    for preview_path in generate_profile_prestige_previews(preview_directory):
        print(preview_path)
else:
    if not TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN has not been configured."
        )
    bot.run(TOKEN)
