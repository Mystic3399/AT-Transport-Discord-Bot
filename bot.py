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


# --------------------------------------------------
# DRIVER DATABASE HELPERS
# --------------------------------------------------

async def find_driver_link(
    trucksbook_name,
):
    if db_pool is None:
        return None

    async with db_pool.acquire() as connection:
        discord_user_id = (
            await connection.fetchval(
                """
                SELECT discord_user_id
                FROM driver_links
                WHERE LOWER(trucksbook_name)
                    = LOWER($1);
                """,
                trucksbook_name,
            )
        )

    if discord_user_id:
        return int(discord_user_id)

    return DRIVER_MAPPINGS.get(
        trucksbook_name
    )


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

            return {
                "status": "added",
                "real_miles": int(real_miles),
            }


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

@bot.command(name="verifytest")
async def verify_test(ctx):
    if ctx.guild is None:
        return

    if ctx.guild.id != GUILD_ID:
        return

    # Management-only protection
    if not member_is_verification_staff(ctx.author):
        await ctx.reply(
            "This test command is restricted to A&T Management.",
            mention_author=False,
        )
        return

    category = ctx.guild.get_channel(
        VERIFICATION_CATEGORY_ID
    )

    if not isinstance(
        category,
        discord.CategoryChannel,
    ):
        await ctx.reply(
            "The A&T verification category could not be found.",
            mention_author=False,
        )
        return

    member = ctx.author

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
            ),

        ctx.guild.me:
            discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True,
            ),
    }

    for role_id in VERIFICATION_STAFF_ROLE_IDS:
        role = ctx.guild.get_role(role_id)

        if role is not None:
            overwrites[role] = (
                discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_messages=True,
                )
            )

    try:
        channel = await ctx.guild.create_text_channel(
            name=f"test-verify-{member.id}",
            category=category,
            overwrites=overwrites,
            reason="A&T onboarding system test",
        )

    except discord.Forbidden:
        await ctx.reply(
            "TEST FAILED: The bot does not have permission to create channels.",
            mention_author=False,
        )
        return

    except discord.HTTPException as error:
        await ctx.reply(
            f"TEST FAILED: Discord returned an error: `{error}`",
            mention_author=False,
        )
        return

    embed = discord.Embed(
        title="🧪 A&T DRIVER VERIFICATION TEST",
        description=(
            f"{member.mention}\n\n"
            "The private driver-verification channel was "
            "created successfully.\n\n"
            "### Permission Test\n"
            "This channel should only be visible to:\n"
            "• You\n"
            "• Owners\n"
            "• Admin\n"
            "• Management\n"
            "• Recruitment Manager\n"
            "• A&T Transport Bot\n\n"
            "No TrucksBook account, mileage, progression "
            "role or driver link has been changed.\n\n"
            "**TEST MODE — SAFE TO DELETE**"
        ),
        colour=discord.Colour.green(),
    )

    embed.set_footer(
        text="A&T Transport LTD • Onboarding Test Mode"
    )

    await channel.send(
        content=member.mention,
        embed=embed,
    )

    await ctx.reply(
        (
            "✅ Verification channel test successful: "
            f"{channel.mention}"
        ),
        mention_author=False,
    )

    print("--------------------------------")
    print("A&T VERIFICATION TEST")
    print(f"Tester: {member} ({member.id})")
    print(f"Test Channel: {channel.id}")
    print("NO DRIVER DATA WAS MODIFIED")
    print("--------------------------------")

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

    if (
        verification["status"]
        != "pending"
    ):
        await ctx.reply(
            (
                "This verification is no longer "
                "waiting for a TrucksBook ID."
            ),
            mention_author=False,
        )
        return

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

    trucksbook_user_id = (
        trucksbook_user_id.strip()
    )

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

    numeric_id = int(
        trucksbook_user_id
    )

    if numeric_id <= 0:
        await ctx.reply(
            (
                "That does not look like a valid "
                "TrucksBook User ID."
            ),
            mention_author=False,
        )
        return

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

        await ctx.reply(
            (
                "There was a database error while "
                "saving the TrucksBook ID. Please "
                "try again shortly."
            ),
            mention_author=False,
        )
        return

    if result == "already_linked":
        await ctx.reply(
            (
                "❌ That TrucksBook User ID is "
                "already linked to another A&T "
                "driver account.\n\n"
                "If you believe this is incorrect, "
                "please ask A&T Management to "
                "review it."
            ),
            mention_author=False,
        )

        await log_verification(
            ctx.guild,
            "⚠️ Duplicate TrucksBook ID Attempt",
            (
                f"**Applicant Discord ID:** "
                f"`{applicant_id}`\n"
                f"**Submitted TrucksBook ID:** "
                f"`{numeric_id}`\n"
                f"**Channel:** {ctx.channel.mention}\n"
                "**Result:** ID already linked"
            ),
        )
        return

    if result == "already_pending":
        await ctx.reply(
            (
                "❌ That TrucksBook User ID is "
                "already being used in another "
                "active verification.\n\n"
                "Please contact A&T Management "
                "if you believe this is an error."
            ),
            mention_author=False,
        )

        await log_verification(
            ctx.guild,
            "⚠️ Duplicate Verification Attempt",
            (
                f"**Applicant Discord ID:** "
                f"`{applicant_id}`\n"
                f"**Submitted TrucksBook ID:** "
                f"`{numeric_id}`\n"
                f"**Channel:** {ctx.channel.mention}\n"
                "**Result:** ID already pending"
            ),
        )
        return

    if result is not True:
        await ctx.reply(
            (
                "I could not save that TrucksBook "
                "ID. Please try again."
            ),
            mention_author=False,
        )
        return

    embed = discord.Embed(
        title=(
            "🔎 TRUCKSBOOK ID RECEIVED"
        ),
        description=(
            f"**TrucksBook User ID:** "
            f"`{numeric_id}`\n\n"
            "Your ID has been saved successfully.\n\n"
            "⚠️ **Your Discord account has NOT "
            "been linked yet.**\n\n"
            "The next verification stage will "
            "check this ID against the TrucksBook "
            "profile and confirm that the account "
            "belongs to an A&T Transport LTD "
            "driver.\n\n"
            "Once that check is enabled, you will "
            "be shown the profile details and "
            "asked to confirm them before any "
            "driver link is created."
        ),
        colour=discord.Colour.gold(),
    )

    embed.set_footer(
        text=(
            "A&T Transport LTD • "
            "Verification Pending"
        )
    )

    await ctx.reply(
        embed=embed,
        mention_author=False,
    )

    await log_verification(
        ctx.guild,
        "🔎 TrucksBook ID Submitted",
        (
            f"**Applicant:** <@{applicant_id}>\n"
            f"**Discord ID:** `{applicant_id}`\n"
            f"**TrucksBook ID:** `{numeric_id}`\n"
            f"**Channel:** {ctx.channel.mention}\n"
            "**Status:** Awaiting profile verification"
        ),
    )

    print("--------------------------------")
    print("TRUCKSBOOK ID SUBMITTED")
    print(
        f"Discord User ID: {applicant_id}"
    )
    print(
        f"TrucksBook User ID: {numeric_id}"
    )
    print("--------------------------------")


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
# TRUCKSBOOK PROFILE TEST
# --------------------------------------------------

@bot.command(name="tbtest")
async def trucksbook_profile_test(
    ctx,
    trucksbook_user_id: str = None,
):
    # ------------------------------------------
    # MANAGEMENT-ONLY DIAGNOSTIC COMMAND
    # ------------------------------------------

    if ctx.guild is None:
        return

    if ctx.guild.id != GUILD_ID:
        return

    if not member_is_verification_staff(ctx.author):
        await ctx.reply(
            "This diagnostic command is restricted "
            "to A&T Management.",
            mention_author=False,
        )
        return

    # ------------------------------------------
    # VALIDATE USER ID
    # ------------------------------------------

    if trucksbook_user_id is None:
        await ctx.reply(
            "Please provide a TrucksBook User ID.\n\n"
            "Example: `!tbtest 553238`",
            mention_author=False,
        )
        return

    trucksbook_user_id = trucksbook_user_id.strip()

    if not trucksbook_user_id.isdigit():
        await ctx.reply(
            "The TrucksBook User ID must contain "
            "numbers only.",
            mention_author=False,
        )
        return

    numeric_id = int(trucksbook_user_id)

    if numeric_id <= 0:
        await ctx.reply(
            "That is not a valid TrucksBook User ID.",
            mention_author=False,
        )
        return

    # ------------------------------------------
    # REQUEST PUBLIC TRUCKSBOOK PROFILE
    # ------------------------------------------

    profile_url = (
        "https://trucksbook.eu/profile/"
        f"{numeric_id}"
    )

    await ctx.reply(
        (
            "🔎 Searching TrucksBook profile "
            f"`{numeric_id}`...\n"
            "No A&T driver data will be changed."
        ),
        mention_author=False,
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
                final_url = str(response.url)

                html = await response.text(
                    errors="replace"
                )

    except Exception as error:
        print("--------------------------------")
        print("TRUCKSBOOK PROFILE SEARCH FAILED")
        print(f"User ID: {numeric_id}")
        print(f"Error: {error}")
        print("--------------------------------")

        await ctx.reply(
            (
                "❌ TrucksBook request failed.\n\n"
                f"**Error:** `{type(error).__name__}`"
            ),
            mention_author=False,
        )
        return

    # ------------------------------------------
    # PARSE COMPLETE PAGE
    # ------------------------------------------

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    page_title = ""

    if soup.title:
        page_title = soup.title.get_text(
            " ",
            strip=True,
        )

    # Remove page elements that are not useful
    # for profile identification.
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
    # SEARCH COMPLETE PROFILE
    # ------------------------------------------

    search_terms = [
        str(numeric_id),
        "Mystical Custom",
        "A & T Transport LTD",
        "company",
        "employee",
        "driver",
        "User Profile",
    ]

    matched_sections = []

    for index, line in enumerate(lines):
        lower_line = line.lower()

        matched_term = None

        for term in search_terms:
            if term.lower() in lower_line:
                matched_term = term
                break

        if matched_term is None:
            continue

        start = max(
            0,
            index - 4,
        )

        end = min(
            len(lines),
            index + 8,
        )

        section = lines[
            start:end
        ]

        section_text = "\n".join(
            section
        )

        if section_text not in matched_sections:
            matched_sections.append(
                section_text
            )

    # ------------------------------------------
    # BUILD DIAGNOSTIC RESULT
    # ------------------------------------------

    if matched_sections:
        diagnostic_text = (
            "\n\n--- MATCH ---\n\n".join(
                matched_sections
            )
        )
    else:
        diagnostic_text = (
            "No requested profile search terms "
            "were found in the readable page text."
        )

    # Keep Discord output within safe limits.
    discord_preview = diagnostic_text[:900]

    # Railway can show more information.
    railway_preview = diagnostic_text[:10000]

    # ------------------------------------------
    # EXTRA DIRECT CHECKS
    # ------------------------------------------

    lower_page = page_text.lower()

    mystic_found = (
        "mystical custom"
        in lower_page
    )

  at_transport_found = (
    "a & t transport ltd"
    in lower_page
)
    )

    user_id_found = (
        str(numeric_id)
        in page_text
    )

    # ------------------------------------------
    # RAILWAY OUTPUT
    # ------------------------------------------

    print("--------------------------------")
    print("TRUCKSBOOK PROFILE SEARCH")
    print(f"Requested User ID: {numeric_id}")
    print(f"HTTP Status: {status_code}")
    print(f"Final URL: {final_url}")
    print(f"HTML Length: {len(html)}")
    print(f"Page Title: {page_title}")
    print(
        "Requested User ID found in page: "
        f"{user_id_found}"
    )
    print(
        "Mystical Custom found: "
        f"{mystic_found}"
    )
    print(
        "A&T Transport found: "
        f"{at_transport_found}"
    )
    print("PROFILE SEARCH RESULTS:")
    print(railway_preview)
    print("--------------------------------")

    # ------------------------------------------
    # DISCORD RESULT
    # ------------------------------------------

    embed = discord.Embed(
        title="🔎 TrucksBook Profile Search",
        colour=(
            discord.Colour.green()
            if status_code == 200
            else discord.Colour.orange()
        ),
    )

    embed.add_field(
        name="Requested User ID",
        value=f"`{numeric_id}`",
        inline=True,
    )

    embed.add_field(
        name="HTTP Status",
        value=f"`{status_code}`",
        inline=True,
    )

    embed.add_field(
        name="Profile ID Detected",
        value=(
            "✅ Yes"
            if user_id_found
            else "❌ No"
        ),
        inline=True,
    )

    embed.add_field(
        name="Mystical Custom",
        value=(
            "✅ Found"
            if mystic_found
            else "❌ Not found"
        ),
        inline=True,
    )

    embed.add_field(
        name="A&T Transport",
        value=(
            "✅ Found"
            if at_transport_found
            else "❌ Not found"
        ),
        inline=True,
    )

    embed.add_field(
        name="HTML Received",
        value=f"`{len(html):,} characters`",
        inline=True,
    )

    embed.add_field(
        name="Relevant Profile Text",
        value=(
            f"```text\n"
            f"{discord_preview}\n"
            f"```"
        ),
        inline=False,
    )

    embed.set_footer(
        text=(
            "READ-ONLY TEST • "
            "No A&T driver data was modified"
        )
    )

    await ctx.send(
        embed=embed
    )


@trucksbook_profile_test.error
async def trucksbook_profile_test_error(
    ctx,
    error,
):
    print(
        "TRUCKSBOOK PROFILE TEST COMMAND ERROR: "
        f"{error}"
    )

    await ctx.reply(
        (
            "❌ The TrucksBook diagnostic command "
            "encountered an error.\n\n"
            "Check the Railway deployment logs."
        ),
        mention_author=False,
    )
    # ------------------------------------------
    # MANAGEMENT-ONLY DIAGNOSTIC COMMAND
    # ------------------------------------------

    if ctx.guild is None:
        return

    if ctx.guild.id != GUILD_ID:
        return

    if not member_is_verification_staff(ctx.author):
        await ctx.reply(
            "This diagnostic command is restricted "
            "to A&T Management.",
            mention_author=False,
        )
        return

    # ------------------------------------------
    # VALIDATE USER ID
    # ------------------------------------------

    if trucksbook_user_id is None:
        await ctx.reply(
            "Please provide a TrucksBook User ID.\n\n"
            "Example: `!tbtest 553238`",
            mention_author=False,
        )
        return

    trucksbook_user_id = trucksbook_user_id.strip()

    if not trucksbook_user_id.isdigit():
        await ctx.reply(
            "The TrucksBook User ID must contain "
            "numbers only.",
            mention_author=False,
        )
        return

    numeric_id = int(trucksbook_user_id)

    if numeric_id <= 0:
        await ctx.reply(
            "That is not a valid TrucksBook User ID.",
            mention_author=False,
        )
        return

    # ------------------------------------------
    # REQUEST PUBLIC TRUCKSBOOK PROFILE
    # ------------------------------------------

    profile_url = (
        "https://trucksbook.eu/profile/"
        f"{numeric_id}"
    )

    await ctx.reply(
        (
            "🔎 Testing TrucksBook profile "
            f"`{numeric_id}`...\n"
            "No A&T driver data will be changed."
        ),
        mention_author=False,
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
                final_url = str(response.url)

                html = await response.text(
                    errors="replace"
                )

    except Exception as error:
        print("--------------------------------")
        print("TRUCKSBOOK PROFILE TEST FAILED")
        print(f"User ID: {numeric_id}")
        print(f"Error: {error}")
        print("--------------------------------")

        await ctx.reply(
            (
                "❌ TrucksBook request failed.\n\n"
                f"**Error:** `{type(error).__name__}`\n\n"
                "Check the Railway logs for the "
                "full diagnostic result."
            ),
            mention_author=False,
        )
        return

    # ------------------------------------------
    # PARSE PAGE
    # ------------------------------------------

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    page_title = ""

    if soup.title:
        page_title = soup.title.get_text(
            " ",
            strip=True,
        )

    page_text = soup.get_text(
        "\n",
        strip=True,
    )

    # Keep diagnostic output short enough
    # for Discord.
    preview = page_text[:1500]

    if not preview:
        preview = "[No readable page text detected]"

    # ------------------------------------------
    # RAILWAY DIAGNOSTIC OUTPUT
    # ------------------------------------------

    print("--------------------------------")
    print("TRUCKSBOOK PROFILE TEST")
    print(f"Requested User ID: {numeric_id}")
    print(f"HTTP Status: {status_code}")
    print(f"Final URL: {final_url}")
    print(f"HTML Length: {len(html)}")
    print(f"Page Title: {page_title}")
    print("PAGE TEXT PREVIEW:")
    print(preview)
    print("--------------------------------")

    # ------------------------------------------
    # DISCORD RESULT
    # ------------------------------------------

    embed = discord.Embed(
        title="🧪 TrucksBook Profile Test",
        colour=(
            discord.Colour.green()
            if status_code == 200
            else discord.Colour.orange()
        ),
    )

    embed.add_field(
        name="Requested User ID",
        value=f"`{numeric_id}`",
        inline=False,
    )

    embed.add_field(
        name="HTTP Status",
        value=f"`{status_code}`",
        inline=True,
    )

    embed.add_field(
        name="HTML Received",
        value=f"`{len(html):,} characters`",
        inline=True,
    )

    embed.add_field(
        name="Page Title",
        value=(
            page_title[:1000]
            if page_title
            else "[None detected]"
        ),
        inline=False,
    )

    embed.add_field(
        name="Detected Page Text",
        value=(
            f"```text\n{preview[:900]}\n```"
        ),
        inline=False,
    )

    embed.set_footer(
        text=(
            "READ-ONLY TEST • "
            "No A&T driver data was modified"
        )
    )

    await ctx.send(
        embed=embed
    )


@trucksbook_profile_test.error
async def trucksbook_profile_test_error(
    ctx,
    error,
):
    print(
        "TRUCKSBOOK PROFILE TEST COMMAND ERROR: "
        f"{error}"
    )

    await ctx.reply(
        (
            "❌ The TrucksBook diagnostic command "
            "encountered an error.\n\n"
            "Check the Railway deployment logs."
        ),
        mention_author=False,
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
