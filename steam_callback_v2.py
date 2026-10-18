"""Standalone Steam callback for A&T V2 (staging only).

Run behind HTTPS at /auth/steam/callback using the SAME PostgreSQL database
as the staging bot. Set DATABASE_URL, AT_STEAM_CALLBACK_URL, AT_STEAM_REALM.
Do not expose this service publicly before reviewing reverse-proxy settings.
"""
import hashlib
import re
import os
from urllib.parse import urlsplit

import asyncpg
from aiohttp import web

import steam_openid_v2
import unified_driver_profiles

POOL_KEY = web.AppKey("pool", object)


async def callback(request):
    # Isolated staging acceptance is read-only for driver registration.
    if os.getenv("AT_V2_ISOLATED_STAGING") == "YES":
        raise web.HTTPServiceUnavailable(
            text="Steam registration writes are disabled during staging acceptance"
        )
    pool = request.app[POOL_KEY] if POOL_KEY in request.app else request.app["pool"]
    values = request.query
    if len(values) > 30 or any(len(values.getall(k)) != 1 for k in values):
        raise web.HTTPBadRequest(text="Invalid Steam callback")
    state = values.get("state", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", state):
        raise web.HTTPBadRequest(text="Missing or malformed authentication state")
    state_hash = hashlib.sha256(state.encode("ascii")).hexdigest()
    async with pool.acquire() as conn:
        record = await conn.fetchrow("""
            SELECT discord_user_id,expected_return_to,
                   (expires_at > NOW()) AS valid,consumed_at
            FROM unified_steam_auth_states WHERE state_hash=$1
        """, state_hash)
    if record is None or not record["valid"] or record["consumed_at"] is not None:
        raise web.HTTPBadRequest(text="Steam login expired or already used")
    expected = record["expected_return_to"]
    configured = os.environ["AT_STEAM_CALLBACK_URL"]
    expected_parts, configured_parts = urlsplit(expected), urlsplit(configured)
    if (expected_parts.scheme != "https" or configured_parts.scheme != "https"
            or expected_parts.netloc != configured_parts.netloc
            or expected_parts.path != configured_parts.path
            or expected_parts.path != "/auth/steam/callback"):
        raise web.HTTPBadRequest(text="Unexpected callback origin or route")
    if (values.get("openid.return_to") != expected
            or values.get("openid.return_to", "").count("state=") != 1):
        raise web.HTTPBadRequest(text="Steam callback mismatch")
    try:
        steam_id = await steam_openid_v2.verify_steam_callback(
            {k: values[k] for k in values}, expected
        )
        await unified_driver_profiles.bind_verified_steam(
            pool, record["discord_user_id"], steam_id, state_hash,
            values.get("openid.response_nonce", "")
        )
    except (ValueError, asyncpg.UniqueViolationError):
        raise web.HTTPBadRequest(text="Steam verification failed or account already linked")
    return web.Response(
        text="Steam account verified for A&T registration. Return to your private Discord channel.",
        content_type="text/plain",
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


async def pool_context(app):
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=5)
    app[POOL_KEY] = pool
    try:
        yield
    finally:
        await pool.close()


def make_app():
    app = web.Application(client_max_size=8192)
    app.cleanup_ctx.append(pool_context)
    app.router.add_get("/auth/steam/callback", callback)
    return app


if __name__ == "__main__":
    web.run_app(make_app(), host="127.0.0.1", port=int(os.getenv("PORT", "8081")))
