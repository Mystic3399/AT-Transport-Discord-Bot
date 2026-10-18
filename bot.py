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

# Fail closed in isolated staging before any bot connection, DB setup, or
# background task can interact with Discord. Production is unaffected.
if os.getenv("AT_V2_ISOLATED_STAGING") == "YES":
    staging_guild = os.getenv("STAGING_GUILD_ID", "").strip()
    if not staging_guild.isdecimal() or int(staging_guild) <= 0:
        raise RuntimeError("Staging requires a valid STAGING_GUILD_ID")
    if GUILD_ID != int(staging_guild):
        raise RuntimeError("Staging GUILD_ID must equal STAGING_GUILD_ID")
    if not os.getenv("DISCORD_TOKEN", "").strip():
        raise RuntimeError("Staging requires DISCORD_TOKEN")
    if not DATABASE_URL:
        raise RuntimeError("Staging requires isolated DATABASE_URL")



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
        int(os.getenv("ROLE_NEW_DRIVER") or "0"),
        "A&T New Driver",
    ),
    (
        5000,
        int(os.getenv("ROLE_ROAD_RUNNER") or "0"),
        "Road Runner",
    ),
    (
        10000,
        int(os.getenv("ROLE_DISTANCE_DRIVER") or "0"),
        "Distance Driver",
    ),
    (
        15000,
        int(os.getenv("ROLE_AURORA_DRIVER") or "0"),
        "Aurora Driver",
    ),
    (
        20000,
        int(os.getenv("ROLE_WOLF_PACK_DRIVER") or "0"),
        "Wolf Pack Driver",
    ),
    (
        25000,
        int(os.getenv("ROLE_VIKING_HAULER") or "0"),
        "Viking Hauler",
    ),
    (
        30000,
        int(os.getenv("ROLE_ELITE_HAULER") or "0"),
        "Elite Hauler",
    ),
    (
        35000,
        int(os.getenv("ROLE_AT_VETERAN") or "0"),
        "A&T Veteran",
    ),
    (
        40000,
        int(os.getenv("ROLE_AT_ROAD_LEGEND") or "0"),
        "A&T Road Legend",
    ),
    (
        50000,
        int(os.getenv("ROLE_BEYOND_HORIZONS") or "0"),
        "Beyond Horizons",
    ),
    (
        75000,
        int(os.getenv("ROLE_HORIZON_WOLF") or "0"),
        "Horizon Wolf",
    ),
    (
        100000,
        int(os.getenv("ROLE_AT_CENTURION") or "0"),
        "A&T Centurion",
    ),
    (
        150000,
        int(os.getenv("ROLE_VIKING_PATHFINDER") or "0"),
        "Viking Pathfinder",
    ),
    (
        200000,
        int(os.getenv("ROLE_ROAD_GUARDIAN") or "0"),
        "Road Guardian",
    ),
    (
        300000,
        int(os.getenv("ROLE_AURORA_PATHFINDER") or "0"),
        "Aurora Pathfinder",
    ),
    (
        400000,
        int(os.getenv("ROLE_VIKING_ROADMASTER") or "0"),
        "Viking Roadmaster",
    ),
    (
        500000,
        int(os.getenv("ROLE_HORIZON_LEGEND") or "0"),
        "Horizon Legend",
    ),
    (
        600000,
        int(os.getenv("ROLE_ALPHA_ROADMASTER") or "0"),
        "Alpha Roadmaster",
    ),
    (
        700000,
        int(os.getenv("ROLE_AURORA_COMMANDER") or "0"),
        "Aurora Commander",
    ),
    (
        800000,
        int(os.getenv("ROLE_GUARDIAN_HORIZON") or "0"),
        "Guardian of the Horizon",
    ),
    (
        900000,
        int(os.getenv("ROLE_BEYOND_HORIZON") or "0"),
        "Beyond the Horizon",
    ),
    (
        1000000,
        int(os.getenv("ROLE_AT_IMMORTAL") or "0"),
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
    "â”â” ğŸ‘‘ LEADERSHIP & STAFF â”â”": {
        "role_names": {
            "Owners", "Owner", "Admin", "Administrator", "Management",
            "Recruitment Manager", "Driver Dispatcher", "Server Moderators",
            "Server Moderator", "Moderator",
        },
    },
    "â”â” ğŸ… DRIVER RANKS & BADGES â”â”": {
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
    "â”â” ğŸ¤ PARTNERS & COMMUNITY â”â”": {
        "role_names": {
            "Alliance Partners", "Alliance Partner", "Community Partner",
            "Partners",
        },
    },
    "â”â” ğŸ® INTERESTS & ACCESS â”â”": {
        "role_names": {"Other Games", "New Member", "F1", "Formula 1"},
    },
    "â”â” ğŸ’œ BOOSTERS & EXTRAS â”â”": {
        "role_names": {"Server Booster", "Booster"},
        "include_boosters": True,
    },
    "â”â” ğŸ¤– BOTS & INTEGRATIONS â”â”": {
        "role_names": {"Bots", "Bot", "Integrations"},
        "include_bots": True,
    },
    "â”â” ğŸ” AGE ROLES â”â”": {
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
staging_trucky_probe_ran = False


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
        async def on_trucky_linked(discord_user_id):
            guild = bot.get_guild(GUILD_ID)
            if guild is not None:
                await sync_unified_driver_roles(guild, discord_user_id)

        runner = web.AppRunner(
            trucky_staging_receiver_v2.create_integrations_app(
                db_pool, on_trucky_linked=on_trucky_linked
            ),
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

@bot.check
async def restrict_commands_to_configured_guild(ctx):
    return ctx.guild is not None and ctx.guild.id == GUILD_ID


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
        "â–ˆ" * filled
        + "â–‘" * (width - filled)
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
        title="ğŸ† A&T TRANSPORT LTD â€” MILESTONE ACHIEVED",
        description=(
            f"Congratulations {driver_display}!\n\n"
            f"ğŸ’ **{milestone_miles:,} REAL MILES**\n"
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
        value=f"ğŸ… **{milestone_rank}**",
        inline=False,
    )
    embed.add_field(
        name="Current Real Total",
        value=f"ğŸš› **{real_miles:,} Real miles**",
        inline=False,
    )

    if next_role is None:
        embed.add_field(
            name="Progression Status",
            value="ğŸŒŒ **Beyond Horizons â€” maximum rank achieved**",
            inline=False,
        )
    else:
        next_miles, _, next_rank = next_role
        miles_remaining = max(next_miles - real_miles, 0)
        embed.add_field(
            name="Next Milestone",
            value=f"ğŸŒ  **{next_miles:,} â€” {next_rank}**",
            inline=True,
        )
        embed.add_field(
            name="Miles Remaining",
            value=f"ğŸ“ **{miles_remaining:,}**",
            inline=True,
        )

    embed.set_footer(
        text="A&T Transport LTD â€¢ Driven Beyond Horizons"
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
        title="ğŸŒŸ A&T PERMANENT ACHIEVEMENT UNLOCKED",
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
            value=f"ğŸ›ï¸ **{hall_tier[2]}** â€” a permanent place in A&T history",
            inline=False,
        )
    if threshold == 40000 and MILEAGE_CLUB_40K_CHANNEL_ID:
        embed.add_field(
            name="40K Reward Hub Unlocked",
            value=f"Visit <#{MILEAGE_CLUB_40K_CHANNEL_ID}> for your milestone rewards.",
            inline=False,
        )
    embed.set_footer(text="A&T Transport LTD â€¢ Permanent Achievement")
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
    medals = {1: "ğŸ¥‡", 2: "ğŸ¥ˆ", 3: "ğŸ¥‰"}
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
            f"{prefix} {label} â€” **{int(row['real_miles']):,} mi**"
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
        title="ğŸš› A&T TRANSPORT LTD â€” DRIVER LEADERBOARD",
        description=(
            "Verified **Real Miles** only. Weekly and monthly rankings show "
            "active A&T drivers; all-time records preserve former drivers."
        ),
        colour=discord.Colour.from_rgb(31, 110, 150),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(
        name="ğŸ“… Top 5 Weekly",
        value=(
            leaderboard_lines(weekly_rows, guild, 5)
            + f"\n\nResets <t:{next_week}:R>"
        ),
        inline=False,
    )
    embed.add_field(
        name="ğŸ—“ï¸ Top 5 Monthly",
        value=(
            leaderboard_lines(monthly_rows, guild, 5)
            + f"\n\nResets <t:{next_month}:R>"
        ),
        inline=False,
    )
    embed.add_field(
        name="ğŸ›ï¸ Top 10 All-Time",
        value=leaderboard_lines(all_time_rows, guild, 10, mark_former=True),
        inline=False,
    )
    embed.set_footer(
        text=(
            "A&T Transport LTD â€¢ UK time â€¢ Ties: earliest qualifying job "
            "then Discord ID â€¢ Automatically updated"
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
    icons = {"Immortal": "ğŸ’", "Elite": "ğŸ‘‘", "Veteran": "âš”ï¸"}
    embeds = []
    remaining_budget = 5200
    for tier_name in ("Immortal", "Elite", "Veteran"):
        tier_rows = [row for row in rows if row["highest_tier"] == tier_name]
        lines = []
        for row in tier_rows:
            status = "ğŸŸ¢ Active" if row["is_active"] else "âšª Former A&T Driver"
            registry = (
                f" **#{int(row['immortal_number']):03d}** |"
                if row["immortal_number"] is not None else ""
            )
            since = row[f"{tier_name.lower()}_since"]
            since_text = discord.utils.format_dt(since, style="D") if since else "Recorded"
            line = (
                f"{registry} <@{row['discord_user_id']}> â€” "
                f"**{int(row['highest_verified_real_miles']):,}** Real Miles\n"
                f"`{row['trucksbook_name']}` â€¢ {status} â€¢ Since {since_text}"
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
        "**A&T Transport LTD â€” Permanent Hall of Fame**\n"
        "Earned with verified Real Miles. Membership history is never erased.\n\n"
        + embeds[0].description
    )
    embeds[-1].set_footer(
        text="Veteran 250K â€¢ Elite 500K â€¢ Immortal 1M â€¢ Active/Former shown separately"
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
        title="ğŸ A&T 40K MILEAGE CLUB REWARD HUB",
        description=(
            "Welcome to the reward hub for drivers who have earned "
            "**40,000 verified A&T Real Miles**.\n\n"
            "Your 40K achievement is permanent and remains recorded even as your "
            "normal progression rank continues to change."
        ),
        colour=discord.Colour.from_rgb(31, 150, 135),
    )
    embed.add_field(name="Permanent 40K Members", value=f"**{int(count):,}**")
    embed.set_footer(text="A&T Transport LTD â€¢ Driven Beyond Horizons")
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
# --------------------------------------------------
# A&T BOT ROTATING STATUS
# --------------------------------------------------

BOT_STATUSES = [
    discord.CustomActivity(
        name="ğŸ“¡ Connecting Everyone Together"
    ),
    discord.Activity(
        type=discord.ActivityType.watching,
        name="ğŸš› A&T Transport LTD"
    ),
    discord.Activity(
        type=discord.ActivityType.watching,
        name="ğŸº over the A&T fleet"
    ),
   discord.Game(
    name="ğŸš› Euro Truck Simulator 2"
    ),
    discord.Game(
    name="ğŸ›£ï¸ American Truck Simulator"
    ),
    discord.CustomActivity(
        name="ğŸŒŒ Driven Beyond Horizons"
    ),
    discord.Activity(
        type=discord.ActivityType.watching,
        name="ğŸ“¦ A&T deliveries"
    ),
    discord.Activity(
        type=discord.ActivityType.watching,
        name="ğŸ—ºï¸ drivers across Europe"
    ),
    discord.Activity(
        type=discord.ActivityType.watching,
        name="ğŸ›£ï¸ drivers across America"
    ),
    discord.CustomActivity(
        name="âš™ï¸ A&T Command Systems Online"
    ),
    discord.Activity(
        type=discord.ActivityType.watching,
        name="ğŸ for the next A&T convoy"
    ),
    discord.Activity(
        type=discord.ActivityType.watching,
        name="ğŸ“Š A&T driver progress"
    ),
]


@tasks.loop(seconds=60)
async def rotate_bot_status():
    """Rotate the A&T Command Discord presence."""

    if not BOT_STATUSES:
        return

    index = rotate_bot_status.current_loop % len(BOT_STATUSES)
    activity = BOT_STATUSES[index]

    try:
        await bot.change_presence(
            status=discord.Status.online,
            activity=activity,
        )
    except Exception as error:
        print(
            f"BOT STATUS UPDATE ERROR: {error}"
        )


@rotate_bot_status.before_loop
async def before_rotate_bot_status():
    await bot.wait_until_ready()
    
@bot.event
async def on_ready():
    global db_pool, unified_schema_ready, staging_trucky_probe_ran

    if not rotate_bot_status.is_running():
        rotate_bot_status.start()

    isolated_staging = os.getenv("AT_V2_ISOLATED_STAGING") == "YES"
    if isolated_staging:
        if len(bot.guilds) != 1 or bot.guilds[0].id != GUILD_ID:
            raise RuntimeError("Staging bot must belong only to the configured test guild")
        if bot.user is not None:
            print(
                f"STAGING BOT IDENTITY bot_id={bot.user.id} guild_id={GUILD_ID}",
                flush=True,
            )

    if db_pool is None:
        try:
            await setup_database()
            await trucky_linking.install_schema(db_pool)
            await unified_driver_profiles.install_schema(db_pool)
            await unified_driver_profiles.backfill_trucksbook_profiles(db_pool)
            unified_schema_ready = True
        except Exception as error:
            print(
                f"DATABASE ERROR: {error}"
            )

    if db_pool is not None and unified_schema_ready:
        await start_public_integrations()

    if isolated_staging:
        import trucky_jobs_probe_v2
        if trucky_jobs_probe_v2.should_run_staging_probe(
            is_staging=True,
            enabled=os.getenv("AT_V2_TRUCKY_SCHEMA_PROBE_ON_START"),
            already_ran=staging_trucky_probe_ran,
        ):
            # Mark before awaiting so Discord reconnects cannot repeat the live
            # read-only API diagnostic within this process.
            staging_trucky_probe_ran = True
            try:
                await trucky_jobs_probe_v2.main()
            except SystemExit:
                print("STAGING TRUCKY SCHEMA PROBE STOPPED", flush=True)
            except Exception as error:
                print(
                    "STAGING TRUCKY SCHEMA PROBE ERROR: "
                    f"{type(error).__name__}",
                    flush=True,
                )
        print("STAGING SAFE MODE: historical role sync and leaderboard startup disabled")
        return

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
        # Keep the commands server-scoped only to avoid duplicate entries
        # from simultaneous global and guild registrations.
        home_guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=home_guild)
        guild_synced = await bot.tree.sync(guild=home_guild)
        bot.tree.clear_commands(guild=None)
        global_synced = await bot.tree.sync()

        print(
            f"Synced {len(guild_synced)} home-server slash command(s) "
            f"and {len(global_synced)} global slash command(s)."
        )

    except Exception as error:
        print(
            "Slash command sync failed: "
            f"{error}"
        )


@bot.event
async def on_member_join(member):
    """Restore earned permanent roles and Active status when a driver returns."""
    if member.guild.id != GUILD_ID or os.getenv("AT_V2_ISOLATED_STAGING") == "YES":
        return
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
    message_guild = getattr(message, "guild", None)
    if message_guild is not None and message_guild.id != GUILD_ID:
        return
    if message_guild is None and getattr(message, "webhook_id", None) is None:
        return
    # Staging observation only: no mky÷{h‘éì¶»§q«^t™\šYšXØ][Ûˆ›ØÙ\ÜÈ[[ŒˆZYÜ˜][Ûˆ\İÈ\ÜËˆ‹Y[[Û—Ø]]ÜQ˜[ÙJBˆ™]\›‚ˆN‚ˆİ\œ™[Û˜[YHH]ØZ]XÚŞWÛY[X™\œÚ\ØY\\‹™\šYWÛY[X™\ŠÛZ[VÈœ›İšY\—ØXØÛİ[ÚY—JBˆ^Ù\
˜[YQ\œ›Ü‹XÚŞWÛY[X™\œÚ\ØY\\‹“Y[X™\œÚ\[˜]˜Z[X›JH\È\œ›Ü‚ˆ]ØZ]İœ™\JˆØ[››İ\›İ™NˆXÚŞHY[X™\œÚ\ÚXÚÈ[˜]˜Z[X›NˆÙ\œ›ÜŸH‹Y[[Û—Ø]]ÜQ˜[ÙJBˆ™]\›‚ˆYˆİ\œ™[Û˜[YH\È›Û™N‚ˆ]ØZ]İœ™\JØ[››İ\›İ™NˆXØÛİ[\È›İÛˆHXİ]™HI•XÚŞH›Üİ\‹ˆ‹Y[[Û—Ø]]ÜQ˜[ÙJBˆ™]\›‚ˆ]ØZ]İœ™\Jˆ•HXÚŞH›Üİ\ˆÛÛ™š\›\ÈY[X™\œÚ\Û›NÈ]Ù\È›İ›İ™HİÛ™\œÚ\ˆ‚ˆHœ™\ÚÚYÛ™YXÚŞHÛÛ\]YZ›Øˆ]™[]\İX]ÚHİX[HY[]H‚ˆ˜[™XYH™\šYšYYH\Èš]™\‹ˆX[X[XÚÛ›İÛYÙ[Y[Ø[››İ\›İ™H]ˆ‹ˆY[[Û—Ø]]ÜQ˜[ÙKˆ
B‚‚˜\Ş[˜ÈYˆŞ[˜×İ[šYšYYÙš]™\—Ü›Û\ÊİZ[\ØÛÜ™İ\Ù\—ÚY
N‚ˆˆˆ“Û™H^\İ[™ÈI•›ÙÜ™\ÜÚ[ÛˆY\ˆ›Üˆ™\šYšYYXÚŞKÕXÚÜĞ›ÛÚÈš]™\œË‚‚ˆ™XYYØXŞH™X[Z[\ÈÚ]İ]Ü™X][™ÈÜˆ™\Ù][™Èš]™\—Ü›ÙÜ™\ÜÈ›İÜË‚ˆXÚŞK[Û›HXØÛİ[È™YÚ[ˆ]H^\İ[™È™]Èš]™\ˆY\ˆ[[™\šYšYYˆZ[XYÙH\È[YÜ˜]Y[ÈHÚ\™YZ[XYÙHYÙ\‹ˆYØXŞH™\šYšYYˆXÚÜĞ›ÛÚÈš]™\œÈ™[XZ[ˆ[YÚX›HÚ]İ]™\]Z\š[™ÈHÙXÛÛ™™YÚ\İ˜][Û‹‚ˆˆˆ‚ˆYˆ—ÜÛÛ\È›Û™N‚ˆ™]\›ˆÈœİ]\Èˆ™]X˜\ÙWİ[˜]˜Z[X›H‹œ›ÛWÛ˜[YHˆ›Û™_Bˆ\Ş[˜ÈÚ]—ÜÛÛ˜XÜ]Z\™J
H\ÈÛÛ›™Xİ[Û‚ˆ™\šYšYYH]ØZ]ÛÛ›™Xİ[Û‹™™]Ú˜[
ˆˆ‚ˆÑSPÕVTÕÊˆÑSPÕH”“ÓH[šYšYYÜ›İšY\—Û[šÜÂˆÒT‘H\ØÛÜ™İ\Ù\—ÚYIHS‘™\šYšYYØ]TÈ“Õ•Sˆ
HÔˆVTÕÊˆÑSPÕH”“ÓHš]™\—Û[šÜÂˆÒT‘H\ØÛÜ™İ\Ù\—ÚYIBˆ
Bˆˆˆ‹\ØÛÜ™İ\Ù\—ÚY
BˆYˆ›İ™\šYšYY‚ˆ™]\›ˆÈœİ]\Èˆ[™\šYšYY‹œ›ÛWÛ˜[YHˆ›Û™_Bˆ™X[ÛZ[\ÈH]ØZ]ÛÛ›™Xİ[Û‹™™]Ú˜[
ˆˆ‚ˆÑSPÕ™X[ÛZ[\È”“ÓHš]™\—Ü›ÙÜ™\ÜÈÒT‘H\ØÛÜ™İ\Ù\—ÚYIBˆˆˆ‹\ØÛÜ™İ\Ù\—ÚY
BˆZ[\ÈHX^
›Ø]
™X[ÛZ[\ÈÜˆ
JBˆ™]\›ˆ]ØZ]Ş[˜×ÛY[X™\—Ü›ÙÜ™\ÜÚ[Û—Ü›ÛJİZ[\ØÛÜ™İ\Ù\—ÚYZ[\ÊB‚‚™Yˆ\×Ø]]Üš\ÙYÜİYÚ[™×ÛİÛ™\Š\ØÛÜ™İ\Ù\—ÚY
N‚ˆˆˆ“X]ÚHİYÚ[™ÈİÛ™\ˆÛ›HÚ[ˆ\ÛÛ]YİYÚ[™È[™ÛÛ™šYÈ\™H˜[Yˆˆˆ‚ˆYˆÜË™Ù][ŠUÕŒ—ÒTÓÓUQÔÕQÒS‘ÈŠHOH–QTÈ‚ˆ™]\›ˆ˜[ÙBˆÛÛ™šYİ\™YÛİÛ™\ˆHÜË™Ù][ŠUÕŒ—ÔÕQÒS‘×ÓÕÓ‘T—ÑTĞÓÔ‘ÒQ‹ˆŠBˆÈ\ØÛÜ™Û›İÙ›ZÙ\È\™HXÚ[X[QÎÈ™Z™XİÚ]\ÜXÙKÚYÛœÈ[™X[›Ü›YYˆÈ˜[Y\È˜]\ˆ[ˆÛÙ\˜Ú[™È[H[È[ˆ]]Üš^˜][ÛˆX]Ú‚ˆYˆ›İÛÛ™šYİ\™YÛİÛ™\‹š\Ø\ØÚZJ
HÜˆ›İÛÛ™šYİ\™YÛİÛ™\‹š\ÙXÚ[X[

N‚ˆ™]\›ˆ˜[ÙBˆYˆ›İMÈH[ŠÛÛ™šYİ\™YÛİÛ™\ŠHHŒ‚ˆ™]\›ˆ˜[ÙBˆ™]\›ˆİŠ\ØÛÜ™İ\Ù\—ÚY
HOHÛÛ™šYİ\™YÛİÛ™\‚‚‚›İ˜ÛÛ[X[™
˜[YOHœİYÚ[™ÚY[]X]Y]ŠB˜\Ş[˜ÈYˆİYÚ[™×ÚY[]WØ]Y]
İ
N‚ˆˆˆ”™XYÛ›HÙ[‹X]Y]ÙˆİX[H[™XÚŞH[šÈİ]H[ˆİYÚ[™Ëˆˆˆ‚ˆYˆÜË™Ù][ŠUÕŒ—ÒTÓÓUQÔÕQÒS‘ÈŠHOH–QTÈ‚ˆ]ØZ]İœ™\Jˆ•\ÈXYÛ›ÜİXÈ\È]˜Z[X›HÛ›H[ˆ\ÛÛ]YI•İYÚ[™Ëˆ‹ˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚ˆYˆİ™İZ[\È›Û™HÜˆİ™İZ[šYOHÕRSÒQ‚ˆ]ØZ]İœ™\Jˆ•\ÙH\ÈXYÛ›ÜİXÈ[ˆHÛÛ™šYİ\™YI•İYÚ[™ÈÙ\™\‹ˆ‹ˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚ˆYˆ›İ\×Ø]]Üš\ÙYÜİYÚ[™×ÛİÛ™\Šİ˜]]Ü‹šY
N‚ˆ]ØZ]İœ™\Jˆ“Û›HHÛÛ™šYİ\™YİYÚ[™ÈİÛ™\ˆØ[ˆ[œÜXİ[šÈİ]\Ëˆ‹ˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚ˆYˆ—ÜÛÛ\È›Û™N‚ˆ]ØZ]İœ™\J•H\ÛÛ]YİYÚ[™È]X˜\ÙH\È[˜]˜Z[X›Kˆ‹Y[[Û—Ø]]ÜQ˜[ÙJBˆ™]\›‚ˆN‚ˆ[\Ü[šYšYYÙš]™\—Ü›Ùš[\Âˆİ]HH]ØZ][šYšYYÙš]™\—Ü›Ùš[\Ë™Ù]İXÚŞWÚY[]WÛ[š×ÙXYÛ›ÜİXÜÊˆ—ÜÛÛİ˜]]Ü‹šYˆ
Bˆ^Ù\^Ù\[Ûˆ\È\œ›Ü‚ˆš[
ˆ”ÕQÒS‘ÈQS•UHUQUT”“Ôˆİ\J\œ›ÜŠK—×Û˜[YW×ßH‹›\ÚUYJBˆ]ØZ]İœ™\J•H™XY[Û›HY[]H]Y]˜Z[Yˆ‹Y[[Û—Ø]]ÜQ˜[ÙJBˆ™]\›‚ˆÛZ[WÜİ]\ÈHİ]VÈXÚŞWØÛZ[WÜİ]\È—HÜˆ››Û™H‚ˆÛZ[WÛ[š×ÛX]ÚHİ]VÈ˜ÛZ[WÛ[š×ÛX]Ú—BˆX]ÚÛX™[HY\ÈˆYˆÛZ[WÛ[š×ÛX]Ú\ÈYH[ÙH››ÈˆYˆÛZ[WÛ[š×ÛX]Ú\È˜[ÙH[ÙH››İØ\XØX›H‚ˆ]ØZ]İœ™\Jˆ”İYÚ[™ÈY[]H]Y]
™XY[Û›JNˆ‚ˆˆ”İX[H™\šYšYY^ÉŞY\ÉÈYˆİ]VÉÜİX[Wİ™\šYšYY	×H[ÙH	Û›ÉßNÈ‚ˆˆ•XÚŞHÛZ[O^ØÛZ[WÜİ]\ßNÈ‚ˆˆ•XÚŞH[šÈ™\šYšYY^ÉŞY\ÉÈYˆİ]VÉİXÚŞWÛ[š×İ™\šYšYY	×H[ÙH	Û›ÉßNÈ‚ˆˆ˜ÛZ[KÛ[šÈX]Ú^ÛX]ÚÛX™[Kˆ›È™YÚ\İ˜][ÛˆÜˆZ[XYÙH]HÚ[™ÙYˆ‚ˆ•\Èİ]\È[Û™HÙ\È›İ›İ™HXÚŞHİÛ™\œÚ\È]İ[™\]Z\™\È‚ˆ˜HÚYÛ™YXÚŞH]™[X]Ú[™ÈH™\šYšYYİX[HY[]Kˆ‹ˆY[[Û—Ø]]ÜQ˜[ÙKˆ
B‚‚›İ˜ÛÛ[X[™
˜[YOHœİYÚ[™Ü›Û\Ş[˜ÈŠB˜\Ş[˜ÈYˆİYÚ[™×Ü›ÛWÜŞ[˜Êİ
N‚ˆˆˆ”ØY™[HŞ[˜ÈHİÛ™\‰ÜÈ^\İ[™È›ÙÜ™\ÜÚ[Ûˆ›ÛH[ˆ\ÛÛ]YİYÚ[™Ëˆˆˆ‚ˆYˆÜË™Ù][ŠUÕŒ—ÒTÓÓUQÔÕQÒS‘ÈŠHOH–QTÈ‚ˆ]ØZ]İœ™\Jˆ•\ÈÛÛ[X[™\È]˜Z[X›HÛ›H[ˆ\ÛÛ]YI•İYÚ[™Ëˆ‹ˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚ˆYˆİ™İZ[\È›Û™HÜˆİ™İZ[šYOHÕRSÒQ‚ˆ]ØZ]İœ™\Jˆ•\ÙH\ÈÛÛ[X[™[ˆHÛÛ™šYİ\™YI•İYÚ[™ÈÙ\™\‹ˆ‹ˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚ˆYˆ›İ\×Ø]]Üš\ÙYÜİYÚ[™×ÛİÛ™\Šİ˜]]Ü‹šY
N‚ˆ]ØZ]İœ™\Jˆ“Û›HHÛÛ™šYİ\™YİYÚ[™ÈİÛ™\ˆØ[ˆŞ[˜Ú›Ûš\ÙH›Û\Ëˆ‹ˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚‚ˆN‚ˆ™\İ[H]ØZ]Ş[˜×İ[šYšYYÙš]™\—Ü›Û\Êİ™İZ[İ˜]]Ü‹šY
Bˆ^Ù\\ØÛÜ™‘›Ü˜šY[‚ˆ]ØZ]İœ™\Jˆ‘\ØÛÜ™[šYYH›ÛH\]KˆÚXÚÈH›İ	ÜÈX[˜YÙH›Û\È‚ˆœ\›Z\ÜÚ[Ûˆ[™XZÙHİ\™H]ÈYÚ\İ›ÛH\ÈX›İ™HHš]™\ˆ›Û\Ëˆ‹ˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚ˆ^Ù\\ØÛÜ™’^Ù\[Û‚ˆ]ØZ]İœ™\Jˆ‘\ØÛÜ™Ûİ[›İÛÛ\]HH›ÛH\]Kˆ›ÈZ[XYÙHÜˆ‚ˆœ™YÚ\İ˜][Ûˆ]HØ\ÈÚ[™ÙYÈHYØZ[ˆ]\‹ˆ‹ˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚ˆ^Ù\^Ù\[Ûˆ\È\œ›Ü‚ˆš[
ˆ”ÕQÒS‘È“ÓHÖSÈT”“Ôˆİ\J\œ›ÜŠK—×Û˜[YW×ßHŠBˆ]ØZ]İœ™\Jˆ”›ÛHŞ[˜Ú›Ûš\Ø][Ûˆ˜Z[Yˆ›ÈZ[XYÙHÜˆ™YÚ\İ˜][Ûˆ]H‚ˆØ\ÈÚ[™ÙYˆ‹ˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚‚ˆİ]\ÈH™\İ[™Ù]
œİ]\ÈŠBˆ›ÛWÛ˜[YHH™\İ[™Ù]
œ›ÛWÛ˜[YHŠBˆYˆİ]\ÈOH\]Y‚ˆY\ÜØYÙHHˆ¸§!HİYÚ[™È›ÛH\ÜÚYÛ™Yİ\]Yˆ
ŠÜ›ÛWÛ˜[Y_JŠ‹ˆ‚ˆ[Yˆİ]\ÈOH˜ÛÜœ™Xİ‚ˆY\ÜØYÙHHˆ¸§!H›Û\È\™H[™XYHÛÜœ™Xİˆ
ŠÜ›ÛWÛ˜[Y_JŠ‹ˆ›ÈÚ[™Ù\È™YYYˆ‚ˆ[Yˆİ]\ÈOHœ›ÛWÛZ\ÜÚ[™È‚ˆY\ÜØYÙHHˆ¸¦¨;î#ÈHÛÛ™šYİ\™Y›ÙÜ™\ÜÚ[Ûˆ›ÛH
ŠÜ›ÛWÛ˜[Y_JŠˆ\ÈZ\ÜÚ[™Èœ›ÛH\ÈÙ\™\‹ˆ‚ˆ[Yˆİ]\ÈOH›Y[X™\—ÛZ\ÜÚ[™È‚ˆY\ÜØYÙHH¸¦¨;î#È[İ\ˆ\ØÛÜ™Y[X™\ˆ™XÛÜ™Ûİ[›İ™H›İ[™[ˆ\ÈÙ\™\‹ˆ‚ˆ[Yˆİ]\ÈOH[™\šYšYY‚ˆY\ÜØYÙHH“›È™\šYšYYš]™\ˆ›Ùš[HØ\È›İ[™È›È›Û\ÈÙ\™HÚ[™ÙYˆ‚ˆ[Yˆİ]\ÈOH™]X˜\ÙWİ[˜]˜Z[X›H‚ˆY\ÜØYÙHH•HİYÚ[™È]X˜\ÙH\È[˜]˜Z[X›NÈ›È›Û\ÈÙ\™HÚ[™ÙYˆ‚ˆ[ÙN‚ˆY\ÜØYÙHH”›ÛHŞ[˜Ú›Ûš\Ø][Ûˆ™]\›™Y[ˆ[™^XİYİ]\ÎÈ›ÈZ[XYÙHØ\ÈÚ[™ÙYˆ‚ˆ]ØZ]İœ™\JY\ÜØYÙKY[[Û—Ø]]ÜQ˜[ÙJB‚‚›İ˜ÛÛ[X[™
˜[YOHXÚŞZ›ØœÜ›Ø™HŠB˜\Ş[˜ÈYˆİYÚ[™×İXÚŞWÚ›Øœ×Ü›Ø™JİYÙNˆ[HJN‚ˆˆˆ”™XY]][XØ]YXÚŞHØÚ[XH[ˆ\ÛÛ]YİYÚ[™ÈÚ]İ]^ÜÚ[™È›Øˆ˜[Y\Ëˆˆˆ‚ˆYˆÜË™Ù][ŠUÕŒ—ÒTÓÓUQÔÕQÒS‘ÈŠHOH–QTÈ‚ˆ]ØZ]İœ™\Jˆ•\ÈXYÛ›ÜİXÈ\È]˜Z[X›HÛ›H[ˆ\ÛÛ]YI•İYÚ[™Ëˆ‹ˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚ˆYˆİ™İZ[\È›Û™HÜˆİ™İZ[šYOHÕRSÒQ‚ˆ]ØZ]İœ™\Jˆ•\ÙH\ÈÛÛ[X[™[ˆHÛÛ™šYİ\™YI•İYÚ[™ÈÙ\™\‹ˆ‹ˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚ˆYˆ›İ\×Ø]]Üš\ÙYÜİYÚ[™×ÛİÛ™\Šİ˜]]Ü‹šY
N‚ˆ]ØZ]İœ™\Jˆ“Û›HHÛÛ™šYİ\™YİYÚ[™ÈİÛ™\ˆØ[ˆ[ˆ\ÈXYÛ›ÜİXËˆ‹ˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚‚ˆ[\ÜXÚŞWÚ›Øœ×Ø\WİŒ‚ˆN‚ˆ^[ØYH]ØZ]XÚŞWÚ›Øœ×Ø\WİŒ‹™™]ÚİXÚŞWÚ›Øœ×ÜYÙJYÙO\YÙJBˆÚ\HHXÚŞWÚ›Øœ×Ø\WİŒ‹š[œÜXİÚ›Øœ×Ü™\ÜÛœÙWÜÚ\J^[ØY
Bˆ^Ù\
˜[YQ\œ›Ü‹XÚŞWÚ›Øœ×Ø\WİŒ‹•XÚŞR›Ø“ÛÚİ\[˜]˜Z[X›JH\È\œ›Ü‚ˆ]ØZ]İœ™\Jˆˆ•XÚŞHØÚ[XHÛÚİ\[˜]˜Z[X›NˆÙ\œ›ÜŸKˆ›È]HØ\ÈÚ[™ÙYˆ‹ˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚ˆ^Ù\^Ù\[Ûˆ\È\œ›Ü‚ˆš[
ˆ”ÕQÒS‘È•PÒÖH“Ğˆ“Ğ‘HT”“Ôˆİ\J\œ›ÜŠK—×Û˜[YW×ßHŠBˆ]ØZ]İœ™\Jˆ•XÚŞHØÚ[XHÛÚİ\˜Z[Yˆ›È]HØ\ÈÚ[™ÙYˆ‹ˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚‚ˆ™[™\™YHœÛÛ‹™[\ÊÚ\KÛÜÚÙ^\ÏUYJBˆYˆ[Š™[™\™Y
HˆMŒ‚ˆ™[™\™YH™[™\™YÎŒMNLH
È¸ )ˆ‚ˆ]ØZ]İœ™\Jˆ]][XØ]YXÚŞH™\ÜÛœÙHÚ\H
šY[˜[Y\È[™]H\\ÈÛ›NÈ‚ˆš›Øˆ˜[Y\È[™š]™\ˆ]Z[È\™HÛZ]Y
N—ˆˆ
È™[™\™YˆY[[Û—Ø]]ÜQ˜[ÙKˆ
B‚‚›İ˜ÛÛ[X[™
˜[YOH˜ÛÛ™š\›\›İšY\ˆŠB˜\Ş[˜ÈYˆÛÛ™š\›Wİ[šYšYYÜ›İšY\Šİ›İšY\ˆİˆH›Û™KİÛ™\œÚ\Ù]šY[˜ÙNˆİˆH›Û™JN‚ˆˆˆ‘š[˜[İY™ˆÛÛ™š\›X][ÛˆY\ˆ[™\[™[İÛ™\œÚ\]šY[˜ÙH™]šY]Ëˆˆˆ‚ˆYˆİ™İZ[\È›Û™HÜˆİ™İZ[šYOHÕRSÒQÜˆ›İY[X™\—Ú\×İ™\šYšXØ][Û—ÜİY™Šİ˜]]ÜŠN‚ˆ™]\›‚ˆYˆ›İšY\ˆOHXÚŞHˆÜˆ—ÜÛÛ\È›Û™N‚ˆ]ØZ]İœ™\J“Û›HXÚŞHİÛ™\œÚ\™]šY]È\Èİ\ÜY\™Kˆ‹Y[[Û—Ø]]ÜQ˜[ÙJBˆ™]\›‚ˆ]ØZ]İœ™\Jˆ“X[X[İÛ™\œÚ\ÛÛ™š\›X][Ûˆ\È\ØX›YˆXÚŞH[šÜÈ\™H™\šYšYY‚ˆ˜]]ÛX]XØ[HÛ›Hœ›ÛHHœ™\ÚÚYÛ™Y›Øˆ]™[]X]Ú\ÈHš]™\‰ÜÈ‚ˆ”İX[HÜ[’QY[]H[™Xİ]™HÛÛ\[HY[X™\œÚ\ˆ‹ˆY[[Û—Ø]]ÜQ˜[ÙKˆ
B‚‚›İšXœšYØÛÛ[X[™
˜[YOH›^\™YÚ\İ˜][Ûˆ‹\ØÜš\[ÛH”š]˜][HšY]È[İ\ˆI•š]™\ˆXØÛİ[™YÚ\İ˜][Ûˆİ]\ÈŠB˜\Ş[˜ÈYˆ^\™YÚ\İ˜][ÛŠİ
N‚ˆYˆİ™İZ[\È›Û™HÜˆİ™İZ[šYOHÕRSÒQ‚ˆ™]\›‚ˆ\Ş[˜ÈYˆ™\ÜÛ™
Y\ÜØYÙJN‚ˆYˆİš[\˜Xİ[Ûˆ\È›İ›Û™N‚ˆ]ØZ]İœ™\JY\ÜØYÙK\[Y\˜[UYKY[[Û—Ø]]ÜQ˜[ÙJBˆ[ÙN‚ˆN‚ˆ]ØZ]İ˜]]Ü‹œÙ[™
Y\ÜØYÙJBˆ]ØZ]İ›Y\ÜØYÙK˜YÜ™XXİ[ÛŠ¼'äêHŠBˆ^Ù\\ØÛÜ™‘›Ü˜šY[‚ˆ]ØZ]İœ™\J”X\ÙH[˜X›H\ÈÜˆ\ÙHHÛ^\™YÚ\İ˜][ÛˆÛ\ÚÛÛ[X[™›ÜˆHš]˜]H™\ÜÛœÙKˆ‹Y[[Û—Ø]]ÜQ˜[ÙJBˆYˆ—ÜÛÛ\È›Û™N‚ˆ]ØZ]™\ÜÛ™
•Hš]™\ˆ]X˜\ÙH\È[\Ü˜\š[H[˜]˜Z[X›KˆX\ÙHHYØZ[ˆÚÜKˆŠBˆ™]\›‚ˆN‚ˆ\Ş[˜ÈÚ]—ÜÛÛ˜XÜ]Z\™J
H\ÈÛÛ›‚ˆˆH]ØZ]ÛÛ›‹™™]Ú›İÊ”ÑSPÕXÚÜØ›ÛÚ×Û˜[YH”“ÓHš]™\—Û[šÜÈÒT‘H\ØÛÜ™İ\Ù\—ÚYIH‹İ˜]]Ü‹šY
BˆˆH]ØZ]ÛÛ›‹™™]Ú›İÊˆˆ”ÑSPÕ›İšY\—ØXØÛİ[ÚYTÈXÚŞWØXØÛİ[ÚYˆ›İšY\—Ù\Ü^WÛ˜[YHTÈXÚŞWÙ\Ü^WÛ˜[YH”“ÓH[šYšYYÜ›İšY\—Û[šÜÂˆÒT‘H\ØÛÜ™İ\Ù\—ÚYIHS‘›İšY\IİXÚŞIÈS‘™\šYšYYØ]TÈ“Õ•Sˆˆ‹İ˜]]Ü‹šY
Bˆ[™[™ÈH]ØZ]ÛÛ›‹™™]Ú›İÊˆˆ”ÑSPÕ›İšY\—ØXØÛİ[ÚYTÈXÚŞWØXØÛİ[ÚYİ]\Âˆ”“ÓH[šYšYYÜ›İšY\—ØÛZ[\ÈÒT‘H\ØÛÜ™İ\Ù\—ÚYIHS‘›İšY\IİXÚŞIÈˆˆ‹İ˜]]Ü‹šY
Bˆ^Ù\^Ù\[Û‚ˆš[
“VT‘QÒTÕUSÓˆXØÛİ[ÛÚİ\˜Z[Y‹›\ÚUYJBˆ]ØZ]™\ÜÛ™
XØÛİ[İ]\È\È[\Ü˜\š[H[˜]˜Z[X›KˆX\ÙHÛÛXİI•X[˜YÙ[Y[Yˆ\ÈÛÛ[Y\ËˆŠBˆ™]\›‚ˆYˆ›İ‚ˆXÚŞHH
ˆ“[šÙYˆİ–ÉİXÚŞWÙ\Ü^WÛ˜[YI×_H
Qİ–ÉİXÚŞWØXØÛİ[ÚY	×_JHˆYˆˆ[ÙBˆˆ”™\]Y\İÜ[™[™ÖÉÜİ]\É×_H
QÜ[™[™ÖÉİXÚŞWØXØÛİ[ÚY	×_JHˆYˆ[™[™È[ÙH“›İ[šÙYŠBˆ]ØZ]™\ÜÛ™
ˆ¼'æ¦È
ŠI•š]™\ˆ™YÚ\İ˜][ÛŠŠ—ˆ‚ˆ•XÚÜĞ›ÛÚÎˆ›İ[šÙYˆ‚ˆˆ•XÚŞNˆİXÚŞ_Wˆ‚ˆ•\ÙH
Š‹Ü™YÚ\İ\ŠŠˆ[ˆÌMMLÍLMÌNÌÌŒLˆÈ[šÈ™\šYšYYXØÛİ[Ëˆ‚ˆ
Bˆ™]\›‚ˆXÚŞHH
ˆ“[šÙYˆİ–ÉİXÚŞWÙ\Ü^WÛ˜[YI×_H
Qİ–ÉİXÚŞWØXØÛİ[ÚY	×_JHˆYˆˆ[ÙBˆˆ”™\]Y\İÜ[™[™ÖÉÜİ]\É×_H
QÜ[™[™ÖÉİXÚŞWØXØÛİ[ÚY	×_JHˆYˆ[™[™È[ÙH“›İ[šÙY
Ü[Û˜[
HŠBˆ]ØZ]™\ÜÛ™
ˆ¼'æ¦È
Š–[İ\ˆI•š]™\ˆ™YÚ\İ˜][ÛŠŠ—•XÚÜĞ›ÛÚÎˆ[šÙY8 %İ–ÉİXÚÜØ›ÛÚ×Û˜[YI×_W•XÚŞNˆİXÚŞ_W–[İ\ˆ^\İ[™ÈZ[XYÙH[™›ÙÜ™\ÜÚ[Ûˆ™[XZ[ˆ[˜Ú[™ÙYˆŠB‚‚›İ˜ÛÛ[X[™
˜[YOH™\šYHŠB˜\Ş[˜ÈYˆ™]\™YÙš]™\—İ™\šYJİ
N‚ˆˆˆ“YØXŞHš]™\ˆÛÛ[X[™ˆ™Y\™XİÈÙXİ\š]H™\šYšXØ][Ûˆ\ÈÙ\\˜]Kˆˆˆ‚ˆYˆİ™İZ[\È›Û™HÜˆİ™İZ[šYOHÕRSÒQ‚ˆ™]\›‚ˆYˆİ˜Ú[›™[šYOHÓ“ĞT‘S‘×ĞÒS“‘SÒQ‚ˆ]ØZ]İœ™\Jˆ‘š]™\ˆÛ˜›Ø\™[™È\È[İ™YÈÜ™YÚ\İ\˜ˆ‚ˆ•\ÙHHÛ\ÚÛÛ[X[™È™XÙZ]™HHš]˜]H™\ÜÛœÙKˆ‚ˆ”ÙXİ\š]H™\šYšXØ][Ûˆ\Ù\È]™\šYX[ˆHÙXİ\š]H™\šYšXØ][ÛˆÚ[›™[ˆ‹ˆY[[Û—Ø]]ÜQ˜[ÙKˆ
B‚›İ˜ÛÛ[X[™
ˆ˜[YOHXÚÜØ›ÛÚÈ‚ŠB˜\Ş[˜ÈYˆİX›Z]İXÚÜØ›ÛÚÊˆİˆXÚÜØ›ÛÚ×İ\Ù\—ÚYˆİˆH›Û™KŠN‚ˆYˆİ™İZ[\È›Û™N‚ˆ™]\›‚‚ˆYˆİ™İZ[šYOHÕRSÒQ‚ˆ™]\›‚‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈ’S‘‘T’Q’PĞUSÓ‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆ™\šYšXØ][ÛˆH
ˆ]ØZ]Ù]İ™\šYšXØ][Û—ØWØÚ[›™[
ˆİ˜Ú[›™[šYˆ
Bˆ
B‚ˆYˆ™\šYšXØ][Ûˆ\È›Û™N‚ˆ]ØZ]İœ™\Jˆ
ˆ•\ÈÛÛ[X[™Ø[ˆÛ›H™H\ÙY‚ˆš[œÚYH[İ\ˆš]˜]HI•š]™\ˆ‚ˆ™\šYšXØ][ÛˆÚ[›™[ˆ‚ˆ
KˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚‚ˆ\XØ[ÚYH[
ˆ™\šYšXØ][Û–È™\ØÛÜ™İ\Ù\—ÚY—Bˆ
B‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈÒPÒÈTÑTˆT“RTÔÒSÓ‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆYˆ
ˆİ˜]]Ü‹šYOH\XØ[ÚYˆ[™›İY[X™\—Ú\×İ™\šYšXØ][Û—ÜİY™Šˆİ˜]]Ü‚ˆ
Bˆ
N‚ˆ]ØZ]İœ™\Jˆ
ˆ“Û›HH\XØ[Üˆ]]Üš\ÙY‚ˆI•X[˜YÙ[Y[Ø[ˆİX›Z]H‚ˆ•XÚÜĞ›ÛÚÈQ›Üˆ\È\XØ][Û‹ˆ‚ˆ
KˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈÒPÒÈ‘T’Q’PĞUSÓˆÕUTÂˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆYˆ™\šYšXØ][Û–Èœİ]\È—HOHœ[™[™È‚ˆ]ØZ]İœ™\Jˆ
ˆ•\È™\šYšXØ][Ûˆ\È›ÈÛ™Ù\ˆ‚ˆØZ][™È›ÜˆHXÚÜĞ›ÛÚÈQˆ‚ˆ
KˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈSQUH•PÒÔĞ“ÓÒÈTÑTˆQˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆYˆXÚÜØ›ÛÚ×İ\Ù\—ÚY\È›Û™N‚ˆ]ØZ]İœ™\Jˆ
ˆ”X\ÙH[˜ÛYH[İ\ˆXÚÜĞ›ÛÚÈ‚ˆ•\Ù\ˆQ——ˆ‚ˆ‘^[\N—ˆ‚ˆ˜]XÚÜØ›ÛÚÈLŒÍM˜‚ˆ
KˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚‚ˆXÚÜØ›ÛÚ×İ\Ù\—ÚYHXÚÜØ›ÛÚ×İ\Ù\—ÚYœİš\

B‚ˆYˆ›İXÚÜØ›ÛÚ×İ\Ù\—ÚYš\ÙYÚ]

N‚ˆ]ØZ]İœ™\Jˆ
ˆ•]Ù\È›İÛÚÈZÙHH˜[Y‚ˆ•XÚÜĞ›ÛÚÈ\Ù\ˆQ——ˆ‚ˆ”X\ÙH[\ˆ[X™\œÈÛ›K—ˆ‚ˆ‘^[\Nˆ]XÚÜØ›ÛÚÈLŒÍM˜‚ˆ
KˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚‚ˆ[Y\šX×ÚYH[
XÚÜØ›ÛÚ×İ\Ù\—ÚY
B‚ˆYˆ[Y\šX×ÚYH‚ˆ]ØZ]İœ™\Jˆ
ˆ•]Ù\È›İÛÚÈZÙHH˜[Y‚ˆ•XÚÜĞ›ÛÚÈ\Ù\ˆQˆ‚ˆ
KˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈÒPÒÈ•PÒÔĞ“ÓÒÈ“Ñ’SBˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆÚXÚÚ[™×ÛY\ÜØYÙHH]ØZ]İœ™\Jˆ
ˆ¼'å#ˆÚXÚÚ[™ÈXÚÜĞ›ÛÚÈ›Ùš[H‚ˆˆ˜Û[Y\šX×ÚYX‹‹——ˆ‚ˆ”X\ÙHØZ]Ú[HI•™\šYšY\È‚ˆHš]™\ˆXØÛİ[ˆ‚ˆ
KˆY[[Û—Ø]]ÜQ˜[ÙKˆ
B‚ˆN‚ˆ›Ùš[HH]ØZ]™]ÚİXÚÜØ›ÛÚ×Ü›Ùš[Jˆ[Y\šX×ÚYˆ
B‚ˆ^Ù\^Ù\[Ûˆ\È\œ›Ü‚ˆš[
ˆ••PÒÔĞ“ÓÒÈ“Ñ’SHT”“Ôˆ‚ˆˆÙ\œ›ÜŸH‚ˆ
B‚ˆ]ØZ]ÚXÚÚ[™×ÛY\ÜØYÙK™Y]
ˆÛÛ[Jˆ¸§c[ˆ[™^XİY\œ›ÜˆØØİ\œ™Y‚ˆÚ[HÚXÚÚ[™ÈXÚÜĞ›ÛÚË——ˆ‚ˆ”X\ÙHHYØZ[ˆÚÜKˆ‚ˆ
Bˆ
Bˆ™]\›‚‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈ“Ñ’SHÓÒÕTRSQˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆYˆ›İ›Ùš[K™Ù]
œİXØÙ\ÜÈŠN‚ˆ™X\ÛÛˆH›Ùš[K™Ù]
ˆœ™X\ÛÛˆ‹ˆ[šÛ›İÛ—Ù\œ›Üˆ‹ˆ
B‚ˆYˆ™X\ÛÛˆOHœ™\]Y\İÙ˜Z[Y‚ˆY\ÜØYÙHH
ˆ¸§cXÚÜĞ›ÛÚÈÛİ[›İ™H™XXÚY‚ˆ˜]H[ÛY[——ˆ‚ˆ”X\ÙHHYØZ[ˆÚÜKˆ‚ˆ
B‚ˆ[Yˆ™X\ÛÛˆOHšÙ\œ›Üˆ‚ˆİ]\×ØÛÙHH›Ùš[K™Ù]
ˆœİ]\×ØÛÙH‹ˆ•[šÛ›İÛˆ‹ˆ
B‚ˆY\ÜØYÙHH
ˆ¸§cXÚÜĞ›ÛÚÈ™]\›™Y[ˆ[™^XİY‚ˆœ™\ÜÛœÙK——ˆ‚ˆˆ’İ]\ÎˆÜİ]\×ØÛÙ_X—ˆ‚ˆ”X\ÙHHYØZ[ˆÚÜKˆ‚ˆ
B‚ˆ[Yˆ™X\ÛÛˆOHœ›Ùš[WÛ›İÙ›İ[™‚ˆY\ÜØYÙHH
ˆ¸§cHXÚÜĞ›ÛÚÈ›Ùš[HÛİ[›İ‚ˆ˜™H™\šYšYY\Ú[™È]\Ù\ˆQ——ˆ‚ˆ”X\ÙHÚXÚÈHQ[™HYØZ[‹ˆ‚ˆ
B‚ˆ[ÙN‚ˆY\ÜØYÙHH
ˆ¸§c]XÚÜĞ›ÛÚÈ›Ùš[HÛİ[‚ˆ››İ™H™\šYšYYˆ‚ˆ
B‚ˆ]ØZ]ÚXÚÚ[™×ÛY\ÜØYÙK™Y]
ˆÛÛ[[Y\ÜØYÙBˆ
B‚ˆ]ØZ]Ù×İ™\šYšXØ][ÛŠˆİ™İZ[ˆ¸§cXÚÜĞ›ÛÚÈ›Ùš[HÚXÚÈ˜Z[Y‹ˆ
ˆˆŠŠ\XØ[ŠŠˆØ\XØ[ÚYO—ˆ‚ˆˆŠŠ‘\ØÛÜ™QŠŠˆØ\XØ[ÚYXˆ‚ˆˆŠŠ”İX›Z]YXÚÜĞ›ÛÚÈQŠŠˆ‚ˆˆ˜Û[Y\šX×ÚYXˆ‚ˆˆŠŠÚ[›™[ŠŠˆ‚ˆˆØİ˜Ú[›™[›Y[[ÛŸWˆ‚ˆˆŠŠ”™X\ÛÛŠŠˆÜ™X\ÛÛŸX‚ˆ
Kˆ
Bˆ™]\›‚‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈVPÕ‘T’Q’QQ“Ñ’SBˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆ›Ùš[WÛ˜[YHH›Ùš[K™Ù]
ˆXÚÜØ›ÛÚ×Û˜[YH‚ˆ
B‚ˆÛÛ\[WÛ˜[YHH›Ùš[K™Ù]
ˆ˜ÛÛ\[WÛ˜[YH‚ˆ
B‚ˆÛÛ\[WÜÜÚ][ÛˆH›Ùš[K™Ù]
ˆ˜ÛÛ\[WÜÜÚ][Ûˆ‚ˆ
B‚ˆ™[Û™Ü×İ×Ø]H›Ùš[K™Ù]
ˆ˜™[Û™Ü×İ×Ø]‹ˆ˜[ÙKˆ
B‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈ‘TURT‘HI•QSP‘T”ÒTˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆYˆ›İ™[Û™Ü×İ×Ø]‚ˆ]ØZ]ÚXÚÚ[™×ÛY\ÜØYÙK™Y]
ˆÛÛ[Jˆ¸§c
ŠI•Y[X™\œÚ\Ûİ[›İ™H‚ˆ™\šYšYYŠŠ——ˆ‚ˆ•HXÚÜĞ›ÛÚÈ›Ùš[HØ\È›İ[™‚ˆ˜]]\È›İİ\œ™[H\İYÚ]‚ˆŠŠH	ˆ˜[œÜÜ
Š‹——ˆ‚ˆ’Yˆ[İH™[Y]™H\È\È[˜ÛÜœ™Xİ‚ˆœX\ÙHÛÛXİI•X[˜YÙ[Y[ˆ‚ˆ
Bˆ
B‚ˆ]ØZ]Ù×İ™\šYšXØ][ÛŠˆİ™İZ[ˆ¸§cI•Y[X™\œÚ\™\šYšXØ][Ûˆ˜Z[Y‹ˆ
ˆˆŠŠ\XØ[ŠŠˆØ\XØ[ÚYO—ˆ‚ˆˆŠŠ‘\ØÛÜ™QŠŠˆØ\XØ[ÚYXˆ‚ˆˆŠŠ•XÚÜĞ›ÛÚÈQŠŠˆÛ[Y\šX×ÚYXˆ‚ˆˆŠŠ‘]XİYš]™\ŠŠˆ‚ˆˆ˜Ü›Ùš[WÛ˜[YHÜˆ	Õ[šÛ›İÛ‰ßXˆ‚ˆˆŠŠ‘]XİYÛÛ\[NŠŠˆ‚ˆˆ˜ØÛÛ\[WÛ˜[YHÜˆ	Ó›Û™IßXˆ‚ˆˆŠŠÚ[›™[ŠŠˆ‚ˆˆØİ˜Ú[›™[›Y[[ÛŸH‚ˆ
Kˆ
Bˆ™]\›‚‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈĞU‘H‘T’Q’QQ•PÒÔĞ“ÓÒÈQˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆN‚ˆ™\İ[H]ØZ]Ø]™WİXÚÜØ›ÛÚ×ÚY
ˆ\XØ[ÚYˆ[Y\šX×ÚYˆ
B‚ˆ^Ù\^Ù\[Ûˆ\È\œ›Ü‚ˆš[
ˆ••PÒÔĞ“ÓÒÈQUPTÑHT”“Ôˆ‚ˆˆÙ\œ›ÜŸH‚ˆ
B‚ˆ]ØZ]ÚXÚÚ[™×ÛY\ÜØYÙK™Y]
ˆÛÛ[Jˆ¸§c\™HØ\ÈH]X˜\ÙH\œ›ÜˆÚ[H‚ˆœØ]š[™ÈH™\šYšYYXÚÜĞ›ÛÚÈ‚ˆ˜XØÛİ[——ˆ‚ˆ”X\ÙHHYØZ[ˆÚÜKˆ‚ˆ
Bˆ
Bˆ™]\›‚‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈTPĞUHS’È“ÕPÕSÓ‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆYˆ™\İ[OH˜[™XYWÛ[šÙY‚ˆ]ØZ]ÚXÚÚ[™×ÛY\ÜØYÙK™Y]
ˆÛÛ[Jˆ¸§c]XÚÜĞ›ÛÚÈ\Ù\ˆQ\È‚ˆ˜[™XYH[šÙYÈ[›İ\ˆI•‚ˆ‘\ØÛÜ™XØÛİ[——ˆ‚ˆ”X\ÙHÛÛXİI•X[˜YÙ[Y[‚ˆšYˆ[İH™[Y]™H\È\È[˜ÛÜœ™Xİˆ‚ˆ
Bˆ
B‚ˆ]ØZ]Ù×İ™\šYšXØ][ÛŠˆİ™İZ[ˆ¸¦¨;î#È\XØ]HXÚÜĞ›ÛÚÈQ][\‹ˆ
ˆˆŠŠ\XØ[ŠŠˆØ\XØ[ÚYO—ˆ‚ˆˆŠŠ‘\ØÛÜ™QŠŠˆØ\XØ[ÚYXˆ‚ˆˆŠŠ•XÚÜĞ›ÛÚÈQŠŠˆÛ[Y\šX×ÚYXˆ‚ˆˆŠŠÚ[›™[ŠŠˆ‚ˆˆØİ˜Ú[›™[›Y[[ÛŸWˆ‚ˆŠŠ”™\İ[ŠŠˆ[™XYH[šÙY‚ˆ
Kˆ
Bˆ™]\›‚‚ˆYˆ™\İ[OH˜[™XYWÜ[™[™È‚ˆ]ØZ]ÚXÚÚ[™×ÛY\ÜØYÙK™Y]
ˆÛÛ[Jˆ¸§c]XÚÜĞ›ÛÚÈ\Ù\ˆQ\È‚ˆ˜[™XYH™Z[™È\ÙY[ˆ[›İ\ˆ‚ˆ˜Xİ]™H™\šYšXØ][Û‹——ˆ‚ˆ”X\ÙHÛÛXİI•X[˜YÙ[Y[‚ˆšYˆ[İH™[Y]™H\È\È[˜ÛÜœ™Xİˆ‚ˆ
Bˆ
B‚ˆ]ØZ]Ù×İ™\šYšXØ][ÛŠˆİ™İZ[ˆ¸¦¨;î#È\XØ]H™\šYšXØ][Ûˆ][\‹ˆ
ˆˆŠŠ\XØ[ŠŠˆØ\XØ[ÚYO—ˆ‚ˆˆŠŠ‘\ØÛÜ™QŠŠˆØ\XØ[ÚYXˆ‚ˆˆŠŠ•XÚÜĞ›ÛÚÈQŠŠˆÛ[Y\šX×ÚYXˆ‚ˆˆŠŠÚ[›™[ŠŠˆ‚ˆˆØİ˜Ú[›™[›Y[[ÛŸWˆ‚ˆŠŠ”™\İ[ŠŠˆ[™XYH[™[™È‚ˆ
Kˆ
Bˆ™]\›‚‚ˆYˆ™\İ[\È›İYN‚ˆ]ØZ]ÚXÚÚ[™×ÛY\ÜØYÙK™Y]
ˆÛÛ[Jˆ¸§cH™\šYšYYXÚÜĞ›ÛÚÈQ‚ˆ˜Ûİ[›İ™HØ]™Y——ˆ‚ˆ”X\ÙHHYØZ[‹ˆ‚ˆ
Bˆ
Bˆ™]\›‚‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈĞU‘H‘T’Q’QQ“Ñ’SHSQH
ÈÕUTÂˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆN‚ˆ\Ş[˜ÈÚ]—ÜÛÛ˜XÜ]Z\™J
H\ÈÛÛ›™Xİ[Û‚ˆ\]WÜ™\İ[H]ØZ]ÛÛ›™Xİ[Û‹™^Xİ]Jˆˆˆ‚ˆTUHš]™\—İ™\šYšXØ][ÛœÂˆÑUˆXÚÜØ›ÛÚ×Û˜[YHH	Kˆİ]\ÈH	ØÛÛ™š\›YY	ËˆÛÛ™š\›YYØ]H“ÕÊ
BˆÒT‘H\ØÛÜ™İ\Ù\—ÚYH	‚ˆS‘XÚÜØ›ÛÚ×İ\Ù\—ÚYH	ÂˆS‘İ]\ÈH	Ü[™[™ÉÎÂˆˆˆ‹ˆ›Ùš[WÛ˜[YKˆ\XØ[ÚYˆ[Y\šX×ÚYˆ
B‚ˆ^Ù\^Ù\[Ûˆ\È\œ›Ü‚ˆš[
ˆ••PÒÔĞ“ÓÒÈ‘T’Q’PĞUSÓˆUPTÑHT”“Ôˆ‚ˆˆÙ\œ›ÜŸH‚ˆ
B‚ˆ]ØZ]ÚXÚÚ[™×ÛY\ÜØYÙK™Y]
ˆÛÛ[Jˆ¸§cHXÚÜĞ›ÛÚÈ›Ùš[HØ\È‚ˆ™\šYšYY]]È™\šYšXØ][Ûˆ‚ˆœ™XÛÜ™Ûİ[›İ™H\]Y——ˆ‚ˆ”X\ÙHÛÛXİI•X[˜YÙ[Y[ˆ‚ˆ
Bˆ
Bˆ™]\›‚‚ˆYˆ\]WÜ™\İ[OH•TUH‚ˆ]ØZ]ÚXÚÚ[™×ÛY\ÜØYÙK™Y]
ˆÛÛ[Jˆ¸§cH™\šYšXØ][Ûˆ™XÛÜ™Ú[™ÙY‚ˆ˜™Y›Ü™HH›Ùš[HÛİ[™HØ]™Y——ˆ‚ˆ”X\ÙHÛÛXİI•X[˜YÙ[Y[ˆ‚ˆ
Bˆ
Bˆ™]\›‚‚ˆÈŒˆÚYİÈÛZ[Nˆ™]\ÙHHİXØÙ\ÜÙ[HÚXÚÙYXÚÜĞ›ÛÚÈXØÛİ[‚ˆÈ^\İ[™ÈXÚÜĞ›ÛÚÈ\›İ˜[™[XZ[œÈ]]Üš]]]™H\š[™È™]K‚ˆN‚ˆ]ØZ][šYšYYÙš]™\—Ü›Ùš[\ËœİX›Z]Ü›İšY\—ØÛZ[Jˆ—ÜÛÛ\XØ[ÚYXÚÜØ›ÛÚÈ‹İŠ[Y\šX×ÚY
K›Ùš[WÛ˜[YBˆ
Bˆ^Ù\^Ù\[Ûˆ\È\œ›Ü‚ˆš[
ˆ•Œˆ•PÒÔĞ“ÓÒÈÒQÕÈÓRSHT”“Ôˆİ\J\œ›ÜŠK—×Û˜[YW×ßNˆÙ\œ›ÜŸHŠBˆÈÈ›İ[˜[Y]HH\İX›\ÚYYØXŞH™\šYšXØ][Ûˆ›İË‚‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈ‘SSÕ‘HÒPÒÒS‘ÈQTÔĞQÑBˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆN‚ˆ]ØZ]ÚXÚÚ[™×ÛY\ÜØYÙK™[]J
Bˆ^Ù\\ØÛÜ™’^Ù\[Û‚ˆ\ÜÂ‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈÒÕÈ’U‘TˆÓÓ‘’T“PUSÓ‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆ\XØ[Hİ™İZ[™Ù]ÛY[X™\Šˆ\XØ[ÚYˆ
B‚ˆ\XØ[İ^H
ˆ\XØ[›Y[[Û‚ˆYˆ\XØ[ˆ[ÙHˆØ\XØ[ÚYOˆ‚ˆ
B‚ˆ[X™YH\ØÛÜ™‘[X™Y
ˆ]OH¸§!H•PÒÔĞ“ÓÒÈ“Ñ’SH‘T’Q’QQ‹ˆ\ØÜš\[ÛJˆˆØ\XØ[İ^W—ˆ‚ˆ–[İ\ˆXÚÜĞ›ÛÚÈ›Ùš[H\È™Y[ˆ‚ˆœİXØÙ\ÜÙ[H™\šYšYYÚ]‚ˆŠŠH	ˆ˜[œÜÜ
Š‹——ˆ‚ˆ”X\ÙHÚXÚÈH[™›Ü›X][Ûˆ™[İÈ‚ˆ˜™Y›Ü™HÛÛ[Z[™Ëˆ‚ˆ
KˆÛÛİ\Y\ØÛÜ™ÛÛİ\‹™Ü™Y[Š
Kˆ
B‚ˆ[X™Y˜YÙšY[
ˆ˜[YOH•XÚÜĞ›ÛÚÈ\Ù\ˆQ‹ˆ˜[YOYˆ˜Û[Y\šX×ÚYX‹ˆ[›[™OUYKˆ
B‚ˆ[X™Y˜YÙšY[
ˆ˜[YOH‘š]™\ˆ‹ˆ˜[YOYˆ˜Ü›Ùš[WÛ˜[YHÜˆ	Õ[šÛ›İÛ‰ßX‹ˆ[›[™OUYKˆ
B‚ˆ[X™Y˜YÙšY[
ˆ˜[YOHI•Y[X™\ˆ‹ˆ˜[YOH¸§!HY\È‹ˆ[›[™OUYKˆ
B‚ˆ[X™Y˜YÙšY[
ˆ˜[YOHÛÛ\[H‹ˆ˜[YOYˆ˜ØÛÛ\[WÛ˜[YHÜˆ	Õ[šÛ›İÛ‰ßX‹ˆ[›[™OUYKˆ
B‚ˆ[X™Y˜YÙšY[
ˆ˜[YOHÛÛ\[HÜÚ][Ûˆ‹ˆ˜[YOYˆ˜ØÛÛ\[WÜÜÚ][ÛˆÜˆ	Ó›İ\İY	ßX‹ˆ[›[™OUYKˆ
B‚ˆ[X™Y˜YÙšY[
ˆ˜[YOH•™\šYšXØ][Ûˆ‹ˆ˜[YOH¸§!H]]ÛX]XØ[H™\šYšYY‹ˆ[›[™OUYKˆ
B‚ˆ[X™Y˜YÙšY[
ˆ˜[YOH‘š[˜[ÛÛ™š\›X][Ûˆ‹ˆ˜[YOJˆ’Yˆ\ÙH]Z[È\™HÛÜœ™Xİ\N—ˆ‚ˆŠŠ˜XÛÛ™š\›X
Š——ˆ‚ˆ–[İ\ˆ\ØÛÜ™XØÛİ[Ú[
Š››İ
Šˆ™H‚ˆœ\›X[™[H[šÙY[[[İHÛÛ™š\›Kˆ‚ˆ
Kˆ[›[™OQ˜[ÙKˆ
B‚ˆ[X™Y˜YÙšY[
ˆ˜[YOH¼'å%ÈÜ[Û˜[XÚŞHXˆ[šÚ[™È‹ˆ˜[YOJˆ•XÚŞH\ÈÜ[Û˜[[™Ù\È›İY™™Xİ[İ\ˆXÚÜĞ›ÛÚÈ™\šYšXØ][Û‹ˆ‚ˆ•È™\]Y\İ[šÚ[™È]\‹\ÙH[[šİXÚŞHPĞÓÕS•ÒQ\Ü^H˜[YX‚ˆš[ˆHI•Ù\™\‹ÜˆÚ[\HÚÚ\\Èİ\ˆ‚ˆI•XÚŞHY[X™\œÚ\]\İ™H™\šYšYY™Y›Ü™H\›İ˜[ˆ‚ˆ
Kˆ[›[™OQ˜[ÙKˆ
B‚ˆ[X™YœÙ]Ù›Ûİ\Šˆ^JˆI•˜[œÜÜ8 (ˆ‚ˆ‘š]™[ˆ™^[Û™Üš^›ÛœÈ‚ˆ
Bˆ
B‚ˆ]ØZ]İœÙ[™
ˆ[X™YY[X™Yˆ
B‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈT“PS‘S•PSQÑSQS•ÑÂˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆ]ØZ]Ù×İ™\šYšXØ][ÛŠˆİ™İZ[ˆ¸§!HXÚÜĞ›ÛÚÈ›Ùš[H™\šYšYY‹ˆ
ˆˆŠŠ\XØ[ŠŠˆØ\XØ[İ^Wˆ‚ˆˆŠŠ‘\ØÛÜ™QŠŠˆØ\XØ[ÚYXˆ‚ˆˆŠŠ•XÚÜĞ›ÛÚÈQŠŠˆÛ[Y\šX×ÚYXˆ‚ˆˆŠŠ‘š]™\ŠŠˆ‚ˆˆ˜Ü›Ùš[WÛ˜[YHÜˆ	Õ[šÛ›İÛ‰ßXˆ‚ˆˆŠŠÛÛ\[NŠŠˆ‚ˆˆ˜ØÛÛ\[WÛ˜[YHÜˆ	Õ[šÛ›İÛ‰ßXˆ‚ˆˆŠŠ”ÜÚ][ÛŠŠˆ‚ˆˆ˜ØÛÛ\[WÜÜÚ][ÛˆÜˆ	Ó›İ\İY	ßXˆ‚ˆˆŠŠÚ[›™[ŠŠˆØİ˜Ú[›™[›Y[[ÛŸWˆ‚ˆŠŠ”İ]\ÎŠŠˆ]ØZ][™Èš]™\ˆÛÛ™š\›X][Ûˆ‚ˆ
Kˆ
B‚ˆš[
‹KKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKHŠBˆš[
••PÒÔĞ“ÓÒÈ“Ñ’SH‘T’Q’QQŠBˆš[
ˆ‘\ØÛÜ™\Ù\ˆQˆØ\XØ[ÚYHŠBˆš[
ˆ•XÚÜĞ›ÛÚÈ\Ù\ˆQˆÛ[Y\šX×ÚYHŠBˆš[
ˆ‘š]™\ˆÜ›Ùš[WÛ˜[Y_HŠBˆš[
ˆÛÛ\[NˆØÛÛ\[WÛ˜[Y_HŠBˆš[
ˆ”ÜÚ][ÛˆØÛÛ\[WÜÜÚ][ÛŸHŠBˆš[
”ÕUTÎˆ]ØZ][™ÈXÛÛ™š\›HŠBˆš[
‹KKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKHŠB‚›İ˜ÛÛ[X[™
ˆ˜[YOH˜ÛÛ™š\›H‚ŠB˜\Ş[˜ÈYˆÛÛ™š\›Wİ™\šYšXØ][ÛŠˆİŠN‚ˆYˆİ™İZ[\È›Û™N‚ˆ™]\›‚‚ˆYˆİ™İZ[šYOHÕRSÒQ‚ˆ™]\›‚‚ˆYˆ—ÜÛÛ\È›Û™N‚ˆ]ØZ]İœ™\Jˆ
ˆ¸§cHI•™\šYšXØ][Ûˆ]X˜\ÙH\È‚ˆ˜İ\œ™[H[˜]˜Z[X›K——ˆ‚ˆ”X\ÙHHYØZ[ˆÚÜKˆ‚ˆ
KˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈ’S‘‘T’Q’PĞUSÓ‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆ™\šYšXØ][ÛˆH
ˆ]ØZ]Ù]İ™\šYšXØ][Û—ØWØÚ[›™[
ˆİ˜Ú[›™[šYˆ
Bˆ
B‚ˆYˆ™\šYšXØ][Ûˆ\È›Û™N‚ˆ]ØZ]İœ™\Jˆ
ˆ•\ÈÛÛ[X[™Ø[ˆÛ›H™H\ÙY‚ˆš[œÚYH[ˆXİ]™HI•š]™\ˆ‚ˆ™\šYšXØ][ÛˆÚ[›™[ˆ‚ˆ
KˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚‚ˆ\XØ[ÚYH[
ˆ™\šYšXØ][Û–È™\ØÛÜ™İ\Ù\—ÚY—Bˆ
B‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈTPĞS•UTÕÓÓ‘’T“HSTÑS‘TÂˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆYˆİ˜]]Ü‹šYOH\XØ[ÚY‚ˆ]ØZ]İœ™\Jˆ
ˆ“Û›HH\XØ[Ø[ˆÛÛ\]H‚ˆZ\ˆİÛˆš]™\ˆ™\šYšXØ][Û‹ˆ‚ˆ
KˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈ‘TURT‘H‘T’Q’QQ“Ñ’SBˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆYˆ™\šYšXØ][Û–Èœİ]\È—HOH˜ÛÛ™š\›YY‚ˆ]ØZ]İœ™\Jˆ
ˆ–[İ\ˆXÚÜĞ›ÛÚÈ›Ùš[H\È›İ‚ˆ˜™Y[ˆ™\šYšYYY]——ˆ‚ˆ”X\ÙHİX›Z][İ\ˆXÚÜĞ›ÛÚÈ‚ˆ•\Ù\ˆQš\œİ\Ú[™Î—ˆ‚ˆ˜]XÚÜØ›ÛÚÈSÕT—ÒQ‚ˆ
KˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚‚ˆXÚÜØ›ÛÚ×Û˜[YHH™\šYšXØ][Û–ÂˆXÚÜØ›ÛÚ×Û˜[YH‚ˆB‚ˆXÚÜØ›ÛÚ×İ\Ù\—ÚYH™\šYšXØ][Û–ÂˆXÚÜØ›ÛÚ×İ\Ù\—ÚY‚ˆB‚ˆYˆ
ˆ›İXÚÜØ›ÛÚ×Û˜[YBˆÜˆXÚÜØ›ÛÚ×İ\Ù\—ÚY\È›Û™Bˆ
N‚ˆ]ØZ]İœ™\Jˆ
ˆ¸§c[İ\ˆ™\šYšXØ][Ûˆ™XÛÜ™\È‚ˆ›Z\ÜÚ[™ÈXÚÜĞ›ÛÚÈ[™›Ü›X][Û‹——ˆ‚ˆ”X\ÙHÛÛXİI•X[˜YÙ[Y[ˆ‚ˆ
KˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚‚ˆXÚÜØ›ÛÚ×İ\Ù\—ÚYH[
ˆXÚÜØ›ÛÚ×İ\Ù\—ÚYˆ
B‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈ“Ô“PSTÑH’U‘TˆSQBˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÂˆÈXÚÜĞ›ÛÚÈ›Ùš[\ÈØ[ˆ\Ü^N‚ˆÈI•^\İXØ[İ\İÛH‚ˆÂˆÈÚ[HH›Ø‹Y[]™\HÙXšÛÚÈØ[ˆ\ÙN‚ˆÈ“^\İXØ[İ\İÛH‹‚ˆÂˆÈİÜ™HHÙXšÛÚËXÛÛ\]X›H˜[YHÛÂˆÈ]]ÛX]XÈZ[XYÙHX]Ú[™ÈÙY\ÈÛÜšÚ[™Ë‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆ[š×Û˜[YHH›Ü›X[\ÙWİXÚÜØ›ÛÚ×Û˜[YJˆXÚÜØ›ÛÚ×Û˜[YBˆ
B‚ˆYˆ›İ[š×Û˜[YN‚ˆ]ØZ]İœ™\Jˆ¸§cH™\šYšYYXÚÜĞ›ÛÚÈš]™\ˆ˜[YH\È[˜[Yˆ‹ˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈÔ‘PUHT“PS‘S•’U‘TˆS’ÂˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆš[˜[Ü™X[ÛZ[\ÈH‚ˆN‚ˆ\Ş[˜ÈÚ]—ÜÛÛ˜XÜ]Z\™J
H\ÈÛÛ›™Xİ[Û‚ˆ\Ş[˜ÈÚ]ÛÛ›™Xİ[Û‹˜[œØXİ[ÛŠ
N‚‚ˆÈØÚÈ[™™KXÚXÚÈH™\šYšXØ][Ûˆ[œÚYHHØ[YBˆÈ˜[œØXİ[Û‹ˆ\ÈXZÙ\È™\X]YØÛÛ˜İ\œ™[XÛÛ™š\›BˆÈ][\ÈØY™H[™™]™[Èİ[H™\šYšXØ][Ûˆ]BˆÈœ›ÛH™Z[™ÈÛÛ[Z]Y‚ˆØÚÙYİ™\šYšXØ][ÛˆH]ØZ]ÛÛ›™Xİ[Û‹™™]Ú›İÊˆˆˆ‚ˆÑSPÕˆİ]\ËˆXÚÜØ›ÛÚ×Û˜[YKˆXÚÜØ›ÛÚ×İ\Ù\—ÚYˆ”“ÓHš]™\—İ™\šYšXØ][ÛœÂˆÒT‘H\ØÛÜ™İ\Ù\—ÚYH	BˆS‘™\šYšXØ][Û—ØÚ[›™[ÚYH	‚ˆ“ÔˆTUNÂˆˆˆ‹ˆ\XØ[ÚYˆİ˜Ú[›™[šYˆ
B‚ˆYˆ
ˆØÚÙYİ™\šYšXØ][Ûˆ\È›Û™BˆÜˆØÚÙYİ™\šYšXØ][Û–Èœİ]\È—HOH˜ÛÛ™š\›YY‚ˆÜˆØÚÙYİ™\šYšXØ][Û–ÈXÚÜØ›ÛÚ×Û˜[YH—BˆOHXÚÜØ›ÛÚ×Û˜[YBˆÜˆ[
ˆØÚÙYİ™\šYšXØ][Û–ÈXÚÜØ›ÛÚ×İ\Ù\—ÚY—Bˆ
HOHXÚÜØ›ÛÚ×İ\Ù\—ÚYˆ
N‚ˆ˜Z\ÙH[[YQ\œ›ÜŠˆ•™\šYšXØ][Ûˆ™XÛÜ™Ú[™ÙY™Y›Ü™HÛÛ\][Û‹ˆ‚ˆ
B‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈÒPÒÈTĞÓÔ‘PĞÓÕS•ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆ^\İ[™×Ù\ØÛÜ™H
ˆ]ØZ]ÛÛ›™Xİ[Û‹™™]Ú›İÊˆˆˆ‚ˆÑSPÕˆXÚÜØ›ÛÚ×Û˜[YKˆXÚÜØ›ÛÚ×İ\Ù\—ÚYˆ”“ÓHš]™\—Û[šÜÂˆÒT‘H\ØÛÜ™İ\Ù\—ÚYH	NÂˆˆˆ‹ˆ\XØ[ÚYˆ
Bˆ
B‚ˆYˆ^\İ[™×Ù\ØÛÜ™\È›İ›Û™N‚ˆ]ØZ]İœ™\Jˆ
ˆ¸§c[İ\ˆ\ØÛÜ™XØÛİ[‚ˆš\È[™XYH[šÙYÈ[ˆ‚ˆI•XÚÜĞ›ÛÚÈ›Ùš[K——ˆ‚ˆ”X\ÙHÛÛXİI•‚ˆ“X[˜YÙ[Y[Yˆ[İH™[Y]™H‚ˆ\È\È[˜ÛÜœ™Xİˆ‚ˆ
KˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈÒPÒÈ•PÒÔĞ“ÓÒÈTÑTˆQˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆ^\İ[™×İ—ÚYH
ˆ]ØZ]ÛÛ›™Xİ[Û‹™™]Ú˜[
ˆˆˆ‚ˆÑSPÕ\ØÛÜ™İ\Ù\—ÚYˆ”“ÓHš]™\—Û[šÜÂˆÒT‘HXÚÜØ›ÛÚ×İ\Ù\—ÚYH	NÂˆˆˆ‹ˆXÚÜØ›ÛÚ×İ\Ù\—ÚYˆ
Bˆ
B‚ˆYˆ^\İ[™×İ—ÚY\È›İ›Û™N‚ˆ]ØZ]İœ™\Jˆ
ˆ¸§c\ÈXÚÜĞ›ÛÚÈXØÛİ[‚ˆš\È[™XYH[šÙYÈ‚ˆ˜[›İ\ˆ\ØÛÜ™XØÛİ[——ˆ‚ˆ”X\ÙHÛÛXİI•‚ˆ“X[˜YÙ[Y[ˆ‚ˆ
KˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈÒPÒÈ’U‘TˆSQBˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆ^\İ[™×Û˜[YHH
ˆ]ØZ]ÛÛ›™Xİ[Û‹™™]Ú˜[
ˆˆˆ‚ˆÑSPÕ\ØÛÜ™İ\Ù\—ÚYˆ”“ÓHš]™\—Û[šÜÂˆÒT‘HÕÑTŠˆ‘QÑVÔ‘TPÑJˆ•’SJXÚÜØ›ÛÚ×Û˜[YJKˆ	×˜WÊ‰—Ê
Êß	
IËˆ	ÉËˆ	ÚIÂˆ
Bˆ
HHÕÑTŠ	JNÂˆˆˆ‹ˆ[š×Û˜[YKˆ
Bˆ
B‚ˆYˆ^\İ[™×Û˜[YH\È›İ›Û™N‚ˆ]ØZ]İœ™\Jˆ
ˆ¸§c\ÈXÚÜĞ›ÛÚÈš]™\ˆ‚ˆš\È[™XYH[šÙYÈ‚ˆ˜[›İ\ˆ\ØÛÜ™XØÛİ[——ˆ‚ˆ”X\ÙHÛÛXİI•‚ˆ“X[˜YÙ[Y[ˆ‚ˆ
KˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈS”ÑT•T“PS‘S•S’ÂˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆ]ØZ]ÛÛ›™Xİ[Û‹™^Xİ]Jˆˆˆ‚ˆS”ÑT•S•Èš]™\—Û[šÜÈ
ˆXÚÜØ›ÛÚ×Û˜[YKˆ\ØÛÜ™İ\Ù\—ÚYˆXÚÜØ›ÛÚ×İ\Ù\—ÚYˆ[šÙYØ]ˆ
BˆSQTÈ
ˆ	Kˆ	‹ˆ	Ëˆ“ÕÊ
Bˆ
NÂˆˆˆ‹ˆ[š×Û˜[YKˆ\XØ[ÚYˆXÚÜØ›ÛÚ×İ\Ù\—ÚYˆ
B‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈS’UPSTÑH’U‘Tˆ“ÑÔ‘TÔÒSÓ‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆ]ØZ]ÛÛ›™Xİ[Û‹™^Xİ]Jˆˆˆ‚ˆS”ÑT•S•Èš]™\—Ü›ÙÜ™\ÜÈ
ˆ\ØÛÜ™İ\Ù\—ÚYˆXÚÜØ›ÛÚ×Û˜[YKˆ™X[ÛZ[\Ëˆ\]YØ]ˆ
BˆSQTÈ
ˆ	Kˆ	‹ˆˆ“ÕÊ
Bˆ
BˆÓˆÓÓ‘“PÕ
\ØÛÜ™İ\Ù\—ÚY
BˆÈ“ÕS‘ÎÂˆˆˆ‹ˆ\XØ[ÚYˆ[š×Û˜[YKˆ
B‚ˆÈ™\Ù\™H[HYÚ][X]H™KY^\İ[™ÈZ[XYÙH™XÛÜ™‚ˆÈH›Ü›X[š\œİ][YHš]™\ˆ™XÙZ]™\È™\›ÈZ[\Ë‚ˆš[˜[Ü™X[ÛZ[\ÈH[
ˆ]ØZ]ÛÛ›™Xİ[Û‹™™]Ú˜[
ˆˆˆ‚ˆÑSPÕ™X[ÛZ[\Âˆ”“ÓHš]™\—Ü›ÙÜ™\ÜÂˆÒT‘H\ØÛÜ™İ\Ù\—ÚYH	NÂˆˆˆ‹ˆ\XØ[ÚYˆ
BˆÜˆˆ
B‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈŒˆUÓRPÈS’Q’QQ•PÒÔĞ“ÓÒÈS’ÂˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈØ[YH˜[œØXİ[Ûˆ\ÈH]]Üš]]]™HYØXŞH[šË‚ˆÈYˆ\È˜Z[Ë›İ™YÚ\İ˜][ÛœÈ›Û˜XÚË‚ˆ]ØZ]ÛÛ›™Xİ[Û‹™^Xİ]Jˆˆˆ‚ˆS”ÑT•S•È[šYšYYÙš]™\—Ü›Ùš[\Âˆ
\ØÛÜ™İ\Ù\—ÚYİ]\ËXİ]˜]YØ]
BˆSQTÈ
	K	ØXİ]™IË“ÕÊ
JBˆÓˆÓÓ‘“PÕ
\ØÛÜ™İ\Ù\—ÚY
HÈTUHÑUˆİ]\ÏIØXİ]™IËˆXİ]˜]YØ]PÓĞSTĞÑJˆ[šYšYYÙš]™\—Ü›Ùš[\Ë˜Xİ]˜]YØ]“ÕÊ
Bˆ
NÂˆˆˆ‹ˆ\XØ[ÚYˆ
Bˆ]ØZ]ÛÛ›™Xİ[Û‹™^Xİ]Jˆˆˆ‚ˆS”ÑT•S•È[šYšYYÜ›İšY\—Û[šÜÂˆ
›İšY\‹›İšY\—ØXØÛİ[ÚY\ØÛÜ™İ\Ù\—ÚYˆ›İšY\—Ù\Ü^WÛ˜[YK™\šYšYYØ]™\šYšYYØJBˆSQTÈ
	İXÚÜØ›ÛÚÉË	K	‹	Ë“ÕÊ
K	
BˆÓˆÓÓ‘“PÕ
›İšY\‹›İšY\—ØXØÛİ[ÚY
HÈ“ÕS‘ÎÂˆˆˆ‹ˆİŠXÚÜØ›ÛÚ×İ\Ù\—ÚY
Kˆ\XØ[ÚYˆ[š×Û˜[YKˆİ˜]]Ü‹šYˆ
BˆŒ—ÛİÛ™\ˆH]ØZ]ÛÛ›™Xİ[Û‹™™]Ú˜[
ˆˆˆ‚ˆÑSPÕ\ØÛÜ™İ\Ù\—ÚY”“ÓH[šYšYYÜ›İšY\—Û[šÜÂˆÒT‘H›İšY\IİXÚÜØ›ÛÚÉÈS‘›İšY\—ØXØÛİ[ÚYIBˆS‘™\šYšYYØ]TÈ“Õ•SÂˆˆˆ‹ˆİŠXÚÜØ›ÛÚ×İ\Ù\—ÚY
Kˆ
BˆYˆŒ—ÛİÛ™\ˆOH\XØ[ÚY‚ˆ˜Z\ÙH[[YQ\œ›ÜŠˆ•[šYšYYXÚÜĞ›ÛÚÈXØÛİ[™[Û™ÜÈÈ[›İ\ˆš]™\‹ˆ‚ˆ
Bˆ]ØZ]ÛÛ›™Xİ[Û‹™^Xİ]Jˆˆˆ‚ˆTUH[šYšYYÜ›İšY\—ØÛZ[\ÂˆÑUİ]\ÏIØ\›İ™Y	ËY[X™\œÚ\ØÛÛ™š\›YYU•QKˆİÛ™\œÚ\ØÛÛ™š\›YYU•QK™]šY]ÙYØOI‹ˆ™]šY]ÙYØ]S“ÕÊ
BˆÒT‘H\ØÛÜ™İ\Ù\—ÚYIHS‘›İšY\IİXÚÜØ›ÛÚÉÂˆS‘›İšY\—ØXØÛİ[ÚYIÈS‘İ]\ÏIÜ[™[™ÉÎÂˆˆˆ‹ˆ\XØ[ÚYˆİ˜]]Ü‹šYˆİŠXÚÜØ›ÛÚ×İ\Ù\—ÚY
Kˆ
Bˆ]ØZ]ÛÛ›™Xİ[Û‹™^Xİ]Jˆˆˆ‚ˆTUH[šYšYYÜ™YÚ\İ˜][Û—ÜÙ\ÜÚ[ÛœÂˆÑUİ]\ÏIØ\›İ™Y	Ë\]YØ]S“ÕÊ
BˆÒT‘H\ØÛÜ™İ\Ù\—ÚYINÂˆˆˆ‹ˆ\XØ[ÚYˆ
B‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈÓÓTUH‘T’Q’PĞUSÓ‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆ\]WÜ™\İ[H
ˆ]ØZ]ÛÛ›™Xİ[Û‹™^Xİ]Jˆˆˆ‚ˆTUHš]™\—İ™\šYšXØ][ÛœÂˆÑUˆİ]\ÈH	ØÛÛ\]Y	ËˆÛÛ\]YØ]H“ÕÊ
BˆÒT‘H\ØÛÜ™İ\Ù\—ÚYH	BˆS‘™\šYšXØ][Û—ØÚ[›™[ÚYH	‚ˆS‘İ]\ÈH	ØÛÛ™š\›YY	ÎÂˆˆˆ‹ˆ\XØ[ÚYˆİ˜Ú[›™[šYˆ
Bˆ
B‚ˆYˆ\]WÜ™\İ[OH•TUH‚ˆ˜Z\ÙH[[YQ\œ›ÜŠˆ•™\šYšXØ][Ûˆ™XÛÜ™Ú[™ÙY‚ˆ˜™Y›Ü™HÛÛ\][Û‹ˆ‚ˆ
B‚ˆ^Ù\\Ş[˜ÜË•[š\]YUš[Û][Û‘\œ›Ü‚ˆ]ØZ]İœ™\Jˆ
ˆ¸§c\È\ØÛÜ™ÜˆXÚÜĞ›ÛÚÈ‚ˆ˜XØÛİ[\È[™XYH™Y[ˆ[šÙY——ˆ‚ˆ”X\ÙHÛÛXİI•X[˜YÙ[Y[ˆ‚ˆ
KˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚‚ˆ^Ù\^Ù\[Ûˆ\È\œ›Ü‚ˆš[
ˆ•‘T’Q’PĞUSÓˆÓÓ‘’T“HT”“Ôˆ‚ˆˆÙ\œ›ÜŸH‚ˆ
B‚ˆ]ØZ]İœ™\Jˆ
ˆ¸§c[İ\ˆ™\šYšXØ][ÛˆÛİ[›İ™H‚ˆ˜ÛÛ\]Y™XØ]\ÙHÙˆH]X˜\ÙH‚ˆ™\œ›Ü‹——ˆ‚ˆ“›È\XØ]H[šÈÚ[™HÜ™X]Yˆ‚ˆ”X\ÙHÛÛXİI•X[˜YÙ[Y[ˆ‚ˆ
KˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈTÔÒQÓˆ‘UÈ’U‘Tˆ“ÑÔ‘TÔÒSÓˆ“ÓBˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆ›ÛWÜ™\İ[H›Û™B‚ˆN‚ˆ›ÛWÜ™\İ[H
ˆ]ØZ]Ş[˜×İ[šYšYYÙš]™\—Ü›Û\Êˆİ™İZ[ˆ\XØ[ÚYˆ
Bˆ
B‚ˆ^Ù\\ØÛÜ™‘›Ü˜šY[‚ˆš[
ˆÓÓ‘’T“H“ÓHT”“Ôˆ‚ˆ›İØ[››İX[˜YÙH\XØ[›Û\Ëˆ‚ˆ
B‚ˆ^Ù\\ØÛÜ™’^Ù\[Ûˆ\È\œ›Ü‚ˆš[
ˆÓÓ‘’T“H“ÓHT”“Ôˆ‚ˆˆÙ\œ›ÜŸH‚ˆ
B‚ˆN‚ˆ]ØZ]Ş[˜×Ü\›X[™[ÜŞ\İ[WÙ›Ü—Ùš]™\Šˆİ™İZ[ˆ\XØ[ÚYˆ[š×Û˜[YKˆš[˜[Ü™X[ÛZ[\Ëˆ[››İ[˜ÙOQ˜[ÙKˆ
Bˆ^Ù\^Ù\[Ûˆ\È\œ›Ü‚ˆš[
ˆÓÓ‘’T“HT“PS‘S•“ÓHT”“ÔˆÙ\œ›ÜŸHŠB‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈÕPĞÑTÔÈQTÔĞQÑBˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆ[X™YH\ØÛÜ™‘[X™Y
ˆ]OH¼'ã¢HI•’U‘Tˆ‘T’Q’PĞUSÓˆÓÓTUH‹ˆ\ØÜš\[ÛJˆˆØİ˜]]Ü‹›Y[[ÛŸW—ˆ‚ˆ–[İ\ˆ\ØÛÜ™XØÛİ[\È›İÈ™Y[ˆ‚ˆœİXØÙ\ÜÙ[H[šÙYÈ[İ\ˆ™\šYšYY‚ˆI•XÚÜĞ›ÛÚÈš]™\ˆ›Ùš[K——ˆ‚ˆ•Ù[ÛÛYHÈ
ŠI•˜[œÜÜ
Š‹ˆ‚ˆ
KˆÛÛİ\Y\ØÛÜ™ÛÛİ\‹™Ü™Y[Š
Kˆ
B‚ˆ[X™Y˜YÙšY[
ˆ˜[YOH•XÚÜĞ›ÛÚÈš]™\ˆ‹ˆ˜[YOYˆ˜İXÚÜØ›ÛÚ×Û˜[Y_X‹ˆ[›[™OUYKˆ
B‚ˆ[X™Y˜YÙšY[
ˆ˜[YOH•XÚÜĞ›ÛÚÈ\Ù\ˆQ‹ˆ˜[YOYˆ˜İXÚÜØ›ÛÚ×İ\Ù\—ÚYX‹ˆ[›[™OUYKˆ
B‚ˆ[X™Y˜YÙšY[
ˆ˜[YOH“Z[XYÙH‹ˆ˜[YOYˆ˜Ùš[˜[Ü™X[ÛZ[\Î‹H™X[Z[\Ø‹ˆ[›[™OUYKˆ
B‚ˆ[X™Y˜YÙšY[
ˆ˜[YOH”›ÙÜ™\ÜÚ[Ûˆ‹ˆ˜[YOH¼'æ¦È
ŠI•™]Èš]™\ŠŠˆ‹ˆ[›[™OUYKˆ
B‚ˆ[X™Y˜YÙšY[
ˆ˜[YOH“Z[XYÙH˜XÚÚ[™È‹ˆ˜[YOJˆ–[İ\ˆ]X[YZ[™È
Š”™X[
ŠˆXÚÜĞ›ÛÚÈ‚ˆš›ØœÈØ[ˆ›İÈ™HYY]]ÛX]XØ[H‚ˆÈ[İ\ˆI•›ÙÜ™\ÜÚ[Û‹ˆ‚ˆ
Kˆ[›[™OQ˜[ÙKˆ
B‚ˆ[X™YœÙ]Ù›Ûİ\Šˆ^JˆI•˜[œÜÜ8 (ˆ‚ˆ‘š]™[ˆ™^[Û™Üš^›ÛœÈ‚ˆ
Bˆ
B‚ˆ]ØZ]İœÙ[™
ˆ[X™YY[X™Yˆ
B‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈT“PS‘S•PSQÑSQS•ÑÂˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆ›ÛWÜİ]\ÈH•[šÛ›İÛˆ‚‚ˆYˆ›ÛWÜ™\İ[‚ˆ›ÛWÜİ]\ÈH
ˆ›ÛWÜ™\İ[™Ù]
œ›ÛWÛ˜[YHŠBˆÜˆ›ÛWÜ™\İ[™Ù]
œİ]\ÈŠBˆÜˆ•[šÛ›İÛˆ‚ˆ
B‚ˆ]ØZ]Ù×İ™\šYšXØ][ÛŠˆİ™İZ[ˆ¼'ã¢Hš]™\ˆ™\šYšXØ][ÛˆÛÛ\]Y‹ˆ
ˆˆŠŠ‘š]™\ŠŠˆØİ˜]]Ü‹›Y[[ÛŸWˆ‚ˆˆŠŠ‘\ØÛÜ™QŠŠˆØ\XØ[ÚYXˆ‚ˆˆŠŠ•XÚÜĞ›ÛÚÈ›Ùš[NŠŠˆ‚ˆˆ˜İXÚÜØ›ÛÚ×Û˜[Y_Xˆ‚ˆˆŠŠ•ÙXšÛÚÈš]™\ˆ˜[YNŠŠˆ‚ˆˆ˜Û[š×Û˜[Y_Xˆ‚ˆˆŠŠ•XÚÜĞ›ÛÚÈQŠŠˆ‚ˆˆ˜İXÚÜØ›ÛÚ×İ\Ù\—ÚYXˆ‚ˆˆŠŠ”İ\[™ÈZ[XYÙNŠŠˆ‚ˆˆ˜Ùš[˜[Ü™X[ÛZ[\Î‹H™X[Z[\Øˆ‚ˆˆŠŠ”›ÙÜ™\ÜÚ[Ûˆ›ÛNŠŠˆ‚ˆˆ˜Ü›ÛWÜİ]\ßXˆ‚ˆŠŠ”İ]\ÎŠŠˆÛÛ\]Y‚ˆ
Kˆ
B‚ˆš[
‹KKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKHŠBˆš[
I•’U‘Tˆ‘T’Q’PĞUSÓˆÓÓTUHŠBˆš[
ˆ‘\ØÛÜ™\Ù\ˆQˆØ\XØ[ÚYHŠBˆš[
ˆˆ•XÚÜĞ›ÛÚÈ›Ùš[Nˆ‚ˆˆİXÚÜØ›ÛÚ×Û˜[Y_H‚ˆ
Bˆš[
ˆˆ•ÙXšÛÚÈš]™\ˆ˜[YNˆ‚ˆˆÛ[š×Û˜[Y_H‚ˆ
Bˆš[
ˆˆ•XÚÜĞ›ÛÚÈ\Ù\ˆQˆ‚ˆˆİXÚÜØ›ÛÚ×İ\Ù\—ÚYH‚ˆ
Bˆš[
ˆ”İ\[™ÈZ[XYÙNˆÙš[˜[Ü™X[ÛZ[\ßHŠBˆš[
‹KKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKHŠB‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈÓÔÑHSTÔT–HÒS“‘SˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚ˆ]ØZ]İœÙ[™
ˆ
ˆ¼'å$ˆ™\šYšXØ][ÛˆÛÛ\]K—ˆ‚ˆ•\Èš]˜]HÚ[›™[Ú[ÛÜÙH‚ˆ˜]]ÛX]XØ[H[ˆ
ŠŒÌÙXÛÛ™ÊŠ‹ˆ‚ˆ
Bˆ
B‚ˆ]ØZ]\ØÛÜ™][ËœÛY\İ[[
ˆ\ØÛÜ™][Ë]Û›İÊ
Bˆ
È×Ú[\Ü×Ê™]][YHŠK[YY[JˆÙXÛÛ™ÏLÌˆ
Bˆ
B‚ˆN‚ˆ]ØZ]İ˜Ú[›™[™[]Jˆ™X\ÛÛJˆI•š]™\ˆ™\šYšXØ][ÛˆÛÛ\]Y‚ˆ
Bˆ
B‚ˆ^Ù\\ØÛÜ™‘›Ü˜šY[‚ˆš[
ˆ•‘T’Q’PĞUSÓˆÒS“‘SSUHT”“Ôˆ‚ˆ“Z\ÜÚ[™ÈX[˜YÙHÚ[›™[È\›Z\ÜÚ[Û‹ˆ‚ˆ
B‚ˆ^Ù\\ØÛÜ™’^Ù\[Ûˆ\È\œ›Ü‚ˆš[
ˆ•‘T’Q’PĞUSÓˆÒS“‘SSUHT”“Ôˆ‚ˆˆÙ\œ›ÜŸH‚ˆ
B‚›İ˜ÛÛ[X[™
ˆ˜[YOH˜Ø[˜Ù[‚ŠB˜\Ş[˜ÈYˆØ[˜Ù[İ™\šYšXØ][ÛŠˆİŠN‚ˆYˆİ™İZ[\È›Û™N‚ˆ™]\›‚‚ˆYˆİ™İZ[šYOHÕRSÒQ‚ˆ™]\›‚‚ˆ™\šYšXØ][ÛˆH
ˆ]ØZ]Ù]İ™\šYšXØ][Û—ØWØÚ[›™[
ˆİ˜Ú[›™[šYˆ
Bˆ
B‚ˆYˆ™\šYšXØ][Ûˆ\È›Û™N‚ˆ™]\›‚‚ˆ\XØ[ÚYH[
ˆ™\šYšXØ][Û–È™\ØÛÜ™İ\Ù\—ÚY—Bˆ
B‚ˆYˆ
ˆİ˜]]Ü‹šYOH\XØ[ÚYˆ[™›İY[X™\—Ú\×İ™\šYšXØ][Û—ÜİY™Šˆİ˜]]Ü‚ˆ
Bˆ
N‚ˆ]ØZ]İœ™\Jˆ
ˆ“Û›HH\XØ[Üˆ]]Üš\ÙY‚ˆI•X[˜YÙ[Y[Ø[ˆØ[˜Ù[\È‚ˆ™\šYšXØ][Û‹ˆ‚ˆ
KˆY[[Û—Ø]]ÜQ˜[ÙKˆ
Bˆ™]\›‚‚ˆ\XØ[Hİ™İZ[™Ù]ÛY[X™\Šˆ\XØ[ÚYˆ
B‚ˆ\XØ[İ^H
ˆ\XØ[›Y[[Û‚ˆYˆ\XØ[ˆ[ÙHˆØ\XØ[ÚYOˆ‚ˆ
B‚ˆ]ØZ]Ø[˜Ù[İ™\šYšXØ][Û—Ü™XÛÜ™
ˆ\XØ[ÚYˆ
B‚ˆ]ØZ]Ù×İ™\šYšXØ][ÛŠˆİ™İZ[ˆ¸§cš]™\ˆ™\šYšXØ][ÛˆØ[˜Ù[Y‹ˆ
ˆˆŠŠ‘š]™\ŠŠˆØ\XØ[İ^Wˆ‚ˆˆŠŠ‘\ØÛÜ™QŠŠˆØ\XØ[ÚYXˆ‚ˆˆŠŠØ[˜Ù[YNŠŠˆ‚ˆˆØİ˜]]Ü‹›Y[[ÛŸWˆ‚ˆˆŠŠÚ[›™[ŠŠˆŞØİ˜Ú[›™[›˜[Y_X‚ˆ
Kˆ
B‚ˆ[X™YH\ØÛÜ™‘[X™Y
ˆ]OJˆ¸§c‘T’Q’PĞUSÓˆĞSÑSQ‚ˆ
Kˆ\ØÜš\[ÛJˆ•\ÈI•š]™\ˆ™\šYšXØ][Ûˆ\È‚ˆ˜™Y[ˆØ[˜Ù[Y——ˆ‚ˆ•\Èš]˜]HÚ[›™[Ú[›İÈ™H‚ˆœ™[[İ™Yˆ‚ˆ
KˆÛÛİ\Y\ØÛÜ™ÛÛİ\‹œ™Y

Kˆ
B‚ˆ]ØZ]İœÙ[™
ˆ[X™YY[X™Yˆ
B‚ˆš[
‹KKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKHŠBˆš[
I•’U‘Tˆ‘T’Q’PĞUSÓˆĞSÑSQŠBˆš[
ˆˆ‘\ØÛÜ™\Ù\ˆQˆØ\XØ[ÚYH‚ˆ
Bˆš[
ˆˆØ[˜Ù[YNˆØİ˜]]ÜŸH‚ˆ
Bˆš[
‹KKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKHŠB‚ˆÈÚ]™H\ØÛÜ™H[ÛY[È\Ü^HBˆÈØ[˜Ù[][ÛˆY\ÜØYÙH™Y›Ü™H[][Û‹‚ˆ[\Ü\Ş[˜Ú[Â‚ˆ]ØZ]\Ş[˜Ú[ËœÛY\
JB‚ˆN‚ˆ]ØZ]İ˜Ú[›™[™[]Jˆ™X\ÛÛJˆI•š]™\ˆ™\šYšXØ][ÛˆØ[˜Ù[Y‚ˆ
Bˆ
Bˆ^Ù\\ØÛÜ™‘›Ü˜šY[‚ˆš[
ˆ“Ó“ĞT‘S‘ÈT”“Ôˆ‚ˆ›İØ[››İ[]H™\šYšXØ][ÛˆÚ[›™[ˆ‚ˆ
Bˆ^Ù\\ØÛÜ™’^Ù\[Ûˆ\È\œ›Ü‚ˆš[
ˆ“Ó“ĞT‘S‘ÈSUHT”“Ôˆ‚ˆˆÙ\œ›ÜŸH‚ˆ
B‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈÓ“ĞT‘S‘ÈÓÓSPS‘T”“ÔˆS‘S‘ÂˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚™YÚ\İ\—Ùš]™\‹™\œ›Ü‚˜\Ş[˜ÈYˆ™YÚ\İ\—Ùš]™\—Ù\œ›ÜŠˆİˆ\œ›Ü‹ŠN‚ˆš[
ˆ•‘T’Q–HÓÓSPS‘T”“Ôˆ‚ˆˆÙ\œ›ÜŸH‚ˆ
B‚‚İX›Z]İXÚÜØ›ÛÚË™\œ›Ü‚˜\Ş[˜ÈYˆİX›Z]İXÚÜØ›ÛÚ×Ù\œ›ÜŠˆİˆ\œ›Ü‹ŠN‚ˆš[
ˆ••PÒÔĞ“ÓÒÈÓÓSPS‘T”“Ôˆ‚ˆˆÙ\œ›ÜŸH‚ˆ
B‚ˆ]ØZ]İœ™\Jˆ
ˆ’HÛİ[‰İ›ØÙ\ÜÈ]XÚÜĞ›ÛÚÈ‚ˆ’QˆX\ÙH\ÙN—ˆ‚ˆ˜]XÚÜØ›ÛÚÈLŒÍM˜‚ˆ
KˆY[[Û—Ø]]ÜQ˜[ÙKˆ
B‚‚Ø[˜Ù[İ™\šYšXØ][Û‹™\œ›Ü‚˜\Ş[˜ÈYˆØ[˜Ù[İ™\šYšXØ][Û—Ù\œ›ÜŠˆİˆ\œ›Ü‹ŠN‚ˆš[
ˆĞSÑSÓÓSPS‘T”“Ôˆ‚ˆˆÙ\œ›ÜŸH‚ˆ
B‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈÕT•“ÕÈ‘PQSÓ“HT•ÓÔ’È‘U’QUÂˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚šYˆ‹K\™[™\‹\›Ùš[K\™]šY]ÜÈˆ[ˆŞ\Ë˜\™İ‚ˆ\™İ[Y[Ú[™^HŞ\Ë˜\™İ‹š[™^
‹K\™[™\‹\›Ùš[K\™]šY]ÜÈŠBˆ™]šY]×Ù\™XİÜHH
ˆ]
Ş\Ë˜\™İ–Ø\™İ[Y[Ú[™^
ÈWJBˆYˆ[ŠŞ\Ë˜\™İŠHˆ\™İ[Y[Ú[™^
ÈBˆ[ÙH]
×Ùš[W×ÊKœ™\ÛÛ™J
Kœ\™[Èœ›Ùš[K\™]šY]ÜÈ‚ˆ
Bˆ›Üˆ™]šY]×Ü][ˆÙ[™\˜]WÜ›Ùš[WÜ™\İYÙWÜ™]šY]ÜÊ™]šY]×Ù\™XİÜJN‚ˆš[
™]šY]×Ü]
B™[ÙN‚ˆYˆ›İÒÑS‚ˆ˜Z\ÙH[[YQ\œ›ÜŠˆ‘TĞÓÔ‘ÕÒÑSˆ\È›İ™Y[ˆÛÛ™šYİ\™Yˆ‚ˆ
Bˆ›İœ[ŠÒÑSŠB