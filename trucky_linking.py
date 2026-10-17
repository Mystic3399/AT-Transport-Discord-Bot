"""Optional Trucky account links. No Trucky write API calls or mileage changes.

Pending claims require a management approval; username possession alone is not
proof of account ownership. Import this module from the bot after review.
"""
import re

SCHEMA = """
CREATE TABLE IF NOT EXISTS trucky_driver_links (
    discord_user_id BIGINT PRIMARY KEY REFERENCES driver_links(discord_user_id),
    trucky_account_id TEXT NOT NULL UNIQUE,
    trucky_display_name TEXT NOT NULL,
    verified_by BIGINT NOT NULL,
    verified_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS trucky_link_requests (
    discord_user_id BIGINT PRIMARY KEY REFERENCES driver_links(discord_user_id),
    trucky_account_id TEXT NOT NULL,
    trucky_display_name TEXT NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','approved','rejected','cancelled')),
    reviewed_by BIGINT,
    reviewed_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS unique_pending_trucky_account
    ON trucky_link_requests (trucky_account_id) WHERE status = 'pending';
"""

def valid_account_id(value):
    """Only accept a provider-confirmed stable ID, never a URL or free-form name."""
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value))

async def install_schema(pool):
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)

async def request_link(pool, discord_user_id, account_id, display_name):
    if not valid_account_id(account_id):
        raise ValueError("Invalid Trucky account ID")
    if not display_name or len(display_name) > 120:
        raise ValueError("Invalid display name")
    async with pool.acquire() as conn:
        async with conn.transaction():
            if not await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM driver_links WHERE discord_user_id=$1)",
                discord_user_id,
            ):
                raise ValueError("Complete TrucksBook verification first")
            if await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM trucky_driver_links WHERE discord_user_id=$1)",
                discord_user_id,
            ):
                raise ValueError("Trucky already linked")
            if await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM trucky_driver_links WHERE trucky_account_id=$1)",
                account_id,
            ):
                raise ValueError("Trucky account already linked to another driver")
            await conn.execute(
                """INSERT INTO trucky_link_requests
                   (discord_user_id,trucky_account_id,trucky_display_name,status,
                    reviewed_by,reviewed_at,requested_at)
                   VALUES($1,$2,$3,'pending',NULL,NULL,NOW())
                   ON CONFLICT(discord_user_id) DO UPDATE SET
                     trucky_account_id=EXCLUDED.trucky_account_id,
                     trucky_display_name=EXCLUDED.trucky_display_name,
                     status='pending',reviewed_by=NULL,reviewed_at=NULL,
                     requested_at=NOW()""",
                discord_user_id, account_id, display_name,
            )

async def approve_link(pool, discord_user_id, moderator_id):
    if discord_user_id == moderator_id:
        raise ValueError("A driver cannot approve their own link")
    async with pool.acquire() as conn:
        async with conn.transaction():
            req = await conn.fetchrow(
                """SELECT * FROM trucky_link_requests
                   WHERE discord_user_id=$1 AND status='pending' FOR UPDATE""",
                discord_user_id,
            )
            if req is None:
                raise ValueError("No pending Trucky request")
            await conn.execute(
                """INSERT INTO trucky_driver_links
                   (discord_user_id,trucky_account_id,trucky_display_name,verified_by)
                   VALUES($1,$2,$3,$4)""",
                discord_user_id, req["trucky_account_id"],
                req["trucky_display_name"], moderator_id,
            )
            await conn.execute(
                """UPDATE trucky_link_requests
                   SET status='approved', reviewed_by=$2, reviewed_at=NOW()
                   WHERE discord_user_id=$1""",
                discord_user_id, moderator_id,
            )
            return dict(req)

async def reject_link(pool, discord_user_id, moderator_id):
    async with pool.acquire() as conn:
        result = await conn.execute(
            """UPDATE trucky_link_requests
               SET status='rejected',reviewed_by=$2,reviewed_at=NOW()
               WHERE discord_user_id=$1 AND status='pending'""",
            discord_user_id, moderator_id,
        )
        return result == "UPDATE 1"
