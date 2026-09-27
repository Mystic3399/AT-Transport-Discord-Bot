import os
import re

import asyncpg
import discord
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
# START BOT
# --------------------------------------------------

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN has not been configured."
    )

bot.run(TOKEN)
