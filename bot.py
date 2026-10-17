Warning: truncated output (original token count: 67655)
Total output lines: 7751

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
from aiohttp import web
from bs4 import BeautifulSoup
from discord.ext import commands, tasks
import trucky_linking
import trucky_membership_adapter
import unified_driver_profiles
import steam_openid_v2
import trucky_staging_receiver_v2

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
public_http_runner = None
unified_schema_ready = False


async def start_public_integrations():
    """Start Railway's HTTP listener after database/schema readiness."""
    global public_http_runner
    port_value = os.getenv("PORT", "").strip()
    if not port_value or public_http_runner is not None:
        return
    try:
        port = int(port_value)
        if not 1 <= port <= 65535 or db_pool is None:
            raise ValueError("Invalid HTTP port or database not ready")
        runner = web.AppRunner(
            trucky_staging_receiver_v2.create_integrations_app(db_pool),
            access_log=None,
        )
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        public_http_runner = runner
        print(f"PUBLIC INTEGRATIONS HTTP LISTENER READY on port {port}")
    except Exception as error:
        print(f"PUBLIC INTEGRATIONS HTTP LISTENER ERROR: {type(error).__name__}: {error}")


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

        # Additive migration: keep existing repair candidates and driver totals.
        await connection.execute(
            """
            ALTER TABLE mileage_repair_candidates
                ADD COLUMN IF NOT EXISTS repair_kind TEXT NOT NULL
                    DEFAULT 'distance_correction',
                ADD COLUMN IF NOT EXISTS source_delivered_at TIMESTAMPTZ;
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

class ATCommandBot(commands.Bot):
    async def close(self):
        global public_http_runner
        if public_http_runner is not None:
            await public_http_runner.cleanup()
            public_http_runner = None
        await super().close()


bot = ATCommandBot(
    command_prefix="!",
    intents=intents,
)


# --------------------------------------------------
# BASIC HELPERS
# --------------------------------------------------

def is_real_statistics(statistics):
    """Accept only verified Real classifications, optionally tagged WoTr.

    TrucksBook event deliveries use 'Real, WoTr'. Never infer validity from
    a route, cargo name or the WoTr tag alone.
    """
    if not isinstance(statistics, str):
        return False
    labels = [label.strip().casefold() for label in statistics.split(",")]
    return sorted(labels) in (["real"], ["real", "wotr"])


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
                # The PostgreSQL lock also protects against overlapp…42655 tokens truncated…    return
    verification = await get_verification_by_channel(ctx.channel.id)
    if verification is None or int(verification["discord_user_id"]) != ctx.author.id:
        await ctx.reply("Use this command in your own private /register channel.", mention_author=False)
        return
    if not account_id or not account_id.isdigit():
        await ctx.reply("Please use `!registertrucky 209123` with your numeric Trucky ID.", mention_author=False)
        return
    try:
        canonical_name = await trucky_membership_adapter.verify_member(account_id)
        if canonical_name is None:
            await ctx.reply("This account is not on the active A&T Trucky roster.", mention_author=False)
            return
        await unified_driver_profiles.submit_provider_claim(
            db_pool, ctx.author.id, "trucky", account_id, canonical_name
        )
    except (ValueError, trucky_membership_adapter.MembershipUnavailable) as error:
        await ctx.reply(f"Trucky verification unavailable: {error}. No claim approved.", mention_author=False)
        return
    except Exception as error:
        print(f"V2 TRUCKY CLAIM ERROR: {type(error).__name__}: {error}")
        await ctx.reply("Could not record your Trucky claim. Please contact management.", mention_author=False)
        return
    await ctx.reply(
        f"Trucky account **{canonical_name}** (ID `{account_id}`) recorded as pending. "
        "Use `!linksteam` to verify your Steam account, then `!steamstatus` to check it. "
        "Automatic linking will only become available when Trucky Steam identity "
        "matching has passed staging checks. No mileage has been imported.",
        mention_author=False,
    )


@bot.command(name="approveprovider")
async def approve_unified_provider(ctx, provider: str = None):
    """Management-only V2 beta approval within the applicant's private channel."""
    if ctx.guild is None or ctx.guild.id != GUILD_ID:
        return
    if not member_is_verification_staff(ctx.author):
        await ctx.reply("Only authorised A&T Management can approve accounts.", mention_author=False)
        return
    if provider not in ("trucksbook", "trucky"):
        await ctx.reply("Use `!approveprovider trucksbook` or `!approveprovider trucky` in the applicant's private channel.", mention_author=False)
        return
    if db_pool is None:
        await ctx.reply("Registration database unavailable.", mention_author=False)
        return
    verification = await get_verification_by_channel(ctx.channel.id)
    if verification is None:
        await ctx.reply("Use this command in the applicant's private verification channel.", mention_author=False)
        return
    applicant_id = int(verification["discord_user_id"])
    if applicant_id == ctx.author.id:
        await ctx.reply("You cannot approve your own account.", mention_author=False)
        return
    claim = await unified_driver_profiles.get_pending_claim(db_pool, applicant_id, provider)
    if claim is None or claim["status"] != "pending":
        await ctx.reply("No pending V2 claim for that provider.", mention_author=False)
        return
    # This beta approval is intentionally restricted to Trucky.
    # TrucksBook must continue through its existing verification/approval flow.
    if provider == "trucksbook":
        await ctx.reply("Use the existing TrucksBook verification process until V2 migration tests pass.", mention_author=False)
        return
    try:
        current_name = await trucky_membership_adapter.verify_member(claim["provider_account_id"])
    except (ValueError, trucky_membership_adapter.MembershipUnavailable) as error:
        await ctx.reply(f"Cannot approve: Trucky membership check unavailable: {error}", mention_author=False)
        return
    if current_name is None:
        await ctx.reply("Cannot approve: account is not on the active A&T Trucky roster.", mention_author=False)
        return
    await ctx.reply(
        "Before approval, management must independently confirm account ownership. "
        "The roster check alone is not proof. Use `!confirmprovider trucky` "
        "only after completing that check.",
        mention_author=False,
    )


async def sync_unified_driver_roles(guild, discord_user_id):
    """One existing A&T progression ladder for verified Trucky/TrucksBook drivers.

    Read legacy real miles without creating or resetting driver_progress rows.
    Trucky-only accounts begin at the existing New Driver tier until verified
    mileage is integrated into the shared mileage ledger. Legacy verified
    TrucksBook drivers remain eligible without requiring a second registration.
    """
    if db_pool is None:
        return {"status": "database_unavailable", "role_name": None}
    async with db_pool.acquire() as connection:
        verified = await connection.fetchval("""
            SELECT EXISTS(
                SELECT 1 FROM unified_provider_links
                WHERE discord_user_id=$1 AND verified_at IS NOT NULL
            ) OR EXISTS(
                SELECT 1 FROM driver_links
                WHERE discord_user_id=$1
            )
        """, discord_user_id)
        if not verified:
            return {"status": "unverified", "role_name": None}
        real_miles = await connection.fetchval("""
            SELECT real_miles FROM driver_progress WHERE discord_user_id=$1
        """, discord_user_id)
    miles = max(0, float(real_miles or 0))
    return await sync_member_progression_role(guild, discord_user_id, miles)


@bot.command(name="confirmprovider")
async def confirm_unified_provider(ctx, provider: str = None, ownership_evidence: str = None):
    """Final staff confirmation after independent ownership evidence review."""
    if ctx.guild is None or ctx.guild.id != GUILD_ID or not member_is_verification_staff(ctx.author):
        return
    if provider != "trucky" or db_pool is None:
        await ctx.reply("V2 beta supports Trucky approval only.", mention_author=False)
        return
    if ownership_evidence != "OWNERSHIP-CHECKED":
        await ctx.reply(
            "Before approval, independently verify account ownership with the applicant. "
            "Then use `!confirmprovider trucky OWNERSHIP-CHECKED`. "
            "Do not use this acknowledgement based only on roster membership.",
            mention_author=False,
        )
        return
    verification = await get_verification_by_channel(ctx.channel.id)
    if verification is None:
        await ctx.reply("Use the applicant's private verification channel.", mention_author=False)
        return
    applicant_id = int(verification["discord_user_id"])
    if applicant_id == ctx.author.id:
        await ctx.reply("Self-approval is not allowed.", mention_author=False)
        return
    claim = await unified_driver_profiles.get_pending_claim(db_pool, applicant_id, provider)
    if claim is None or claim["status"] != "pending":
        await ctx.reply("No pending Trucky claim.", mention_author=False)
        return
    try:
        current_name = await trucky_membership_adapter.verify_member(claim["provider_account_id"])
        if current_name is None:
            await ctx.reply("Account is not on A&T's Trucky roster; approval denied.", mention_author=False)
            return
        await unified_driver_profiles.approve_provider_claim(
            db_pool, applicant_id, provider, ctx.author.id,
            membership_confirmed=True, ownership_confirmed=True
        )
    except (ValueError, trucky_membership_adapter.MembershipUnavailable, asyncpg.UniqueViolationError) as error:
        await ctx.reply(f"Approval failed: {error}", mention_author=False)
        return
    except Exception as error:
        print(f"V2 APPROVAL ERROR: {type(error).__name__}: {error}")
        await ctx.reply("Approval could not be completed; contact management.", mention_author=False)
        return
    try:
        role_result = await sync_unified_driver_roles(ctx.guild, applicant_id)
    except (discord.Forbidden, discord.HTTPException) as error:
        print(f"UNIFIED ROLE SYNC ERROR: {type(error).__name__}: {error}")
        role_result = {"status": "role_error", "role_name": None}
    await ctx.reply(
        f"Trucky verified for <@{applicant_id}>. Unified driver profile active. "
        f"Shared A&T progression role: {role_result['status']} "
        f"({role_result.get('role_name') or 'not assigned'}). "
        "Existing TrucksBook mileage was preserved.",
        mention_author=False,
    )


@bot.hybrid_command(name="myregistration", description="Privately view your A&T driver account registration status")
async def myregistration(ctx):
    if ctx.guild is None or ctx.guild.id != GUILD_ID:
        return
    async def respond(message):
        if ctx.interaction is not None:
            await ctx.reply(message, ephemeral=True, mention_author=False)
        else:
            try:
                await ctx.author.send(message)
                await ctx.message.add_reaction("📩")
            except discord.Forbidden:
                await ctx.reply("Please enable DMs or use the /myregistration slash command for a private response.", mention_author=False)
    if db_pool is None:
        await respond("The driver database is temporarily unavailable. Please try again shortly.")
        return
    try:
        async with db_pool.acquire() as conn:
            tb = await conn.fetchrow("SELECT trucksbook_name FROM driver_links WHERE discord_user_id=$1", ctx.author.id)
            tr = await conn.fetchrow("SELECT trucky_account_id, trucky_display_name FROM trucky_driver_links WHERE discord_user_id=$1", ctx.author.id)
            pending = await conn.fetchrow("SELECT trucky_account_id, status FROM trucky_link_requests WHERE discord_user_id=$1 ORDER BY requested_at DESC LIMIT 1", ctx.author.id)
    except Exception:
        print("MYREGISTRATION: account lookup failed", flush=True)
        await respond("Account status is temporarily unavailable. Please contact A&T Management if this continues.")
        return
    if not tb:
        await respond("🚛 **A&T Driver Registration**\nTrucksBook: Not linked. Use **/register** in <#1553652697184473210> to begin private registration.")
        return
    trucky = (f"Linked: {tr['trucky_display_name']} (ID {tr['trucky_account_id']})" if tr else
              f"Request {pending['status']} (ID {pending['trucky_account_id']})" if pending else "Not linked (optional)")
    await respond(f"🚛 **Your A&T Driver Registration**\nTrucksBook: Linked — {tb['trucksbook_name']}\nTrucky: {trucky}\nYour existing mileage and progression remain unchanged.")


@bot.command(name="verify")
async def retired_driver_verify(ctx):
    """Legacy driver command: redirect; security verification is separate."""
    if ctx.guild is None or ctx.guild.id != GUILD_ID:
        return
    if ctx.channel.id == ONBOARDING_CHANNEL_ID:
        await ctx.reply(
            "Driver onboarding has moved to `/register`. "
            "Use the slash command to receive a private response. "
            "Security verification uses `!verify` in the Security Verification channel.",
            mention_author=False,
        )

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

    # V2 shadow claim: reuse the successfully checked TrucksBook account.
    # Existing TrucksBook approval remains authoritative during beta.
    try:
        await unified_driver_profiles.submit_provider_claim(
            db_pool, applicant_id, "trucksbook", str(numeric_id), profile_name
        )
    except Exception as error:
        print(f"V2 TRUCKSBOOK SHADOW CLAIM ERROR: {type(error).__name__}: {error}")
        # Do not invalidate the established legacy verification flow.

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

    embed.add_field(
        name="🔗 Optional Trucky Hub Linking",
        value=(
            "Trucky is optional and does not affect your TrucksBook verification. "
            "To request linking later, use `!linktrucky ACCOUNT_ID Display Name` "
            "in the A&T server, or simply skip this step. "
            "A&T Trucky membership must be verified before approval."
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
                # V2: ATOMIC UNIFIED TRUCKSBOOK LINK
                # ----------------------------------
                # Same transaction as the authoritative legacy link.
                # If this fails, both registrations roll back.
                await connection.execute(
                    """
                    INSERT INTO unified_driver_profiles
                        (discord_user_id,status,activated_at)
                    VALUES ($1,'active',NOW())
                    ON CONFLICT(discord_user_id) DO UPDATE SET
                        status='active',
                        activated_at=COALESCE(
                            unified_driver_profiles.activated_at,NOW()
                        );
                    """,
                    applicant_id,
                )
                await connection.execute(
                    """
                    INSERT INTO unified_provider_links
                        (provider,provider_account_id,discord_user_id,
                         provider_display_name,verified_at,verified_by)
                    VALUES ('trucksbook',$1,$2,$3,NOW(),$4)
                    ON CONFLICT(provider,provider_account_id) DO NOTHING;
                    """,
                    str(trucksbook_user_id),
                    applicant_id,
                    link_name,
                    ctx.author.id,
                )
                v2_owner = await connection.fetchval(
                    """
                    SELECT discord_user_id FROM unified_provider_links
                    WHERE provider='trucksbook' AND provider_account_id=$1
                      AND verified_at IS NOT NULL;
                    """,
                    str(trucksbook_user_id),
                )
                if v2_owner != applicant_id:
                    raise RuntimeError(
                        "Unified TrucksBook account belongs to another driver."
                    )
                await connection.execute(
                    """
                    UPDATE unified_provider_claims
                    SET status='approved',membership_confirmed=TRUE,
                        ownership_confirmed=TRUE,reviewed_by=$2,
                        reviewed_at=NOW()
                    WHERE discord_user_id=$1 AND provider='trucksbook'
                      AND provider_account_id=$3 AND status='pending';
                    """,
                    applicant_id,
                    ctx.author.id,
                    str(trucksbook_user_id),
                )
                await connection.execute(
                    """
                    UPDATE unified_registration_sessions
                    SET status='approved',updated_at=NOW()
                    WHERE discord_user_id=$1;
                    """,
                    applicant_id,
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
            await sync_unified_driver_roles(
                ctx.guild,
                applicant_id,
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

@register_driver.error
async def register_driver_error(
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
