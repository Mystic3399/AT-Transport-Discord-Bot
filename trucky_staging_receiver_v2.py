"""Isolated aiohttp endpoint for Trucky signed webhook schema diagnostics.

Signed receipts may be stored as minimal metadata when a database pool is
provided. No raw payload, driver identity, role write, or mileage award is
stored or applied. The same receiver can be mounted by the bot's HTTP service.
"""
import hashlib
import os
from aiohttp import web
from trucky_signed_webhook_v2 import verify_trucky_webhook, InvalidTruckyWebhook


async def receive_trucky_webhook(request):
    secret = os.getenv("TRUCKY_WEBHOOK_SIGNING_SECRET", "")
    if not secret:
        raise web.HTTPServiceUnavailable(text="Webhook verification not configured")
    if request.content_length is not None and request.content_length > 1024 * 1024:
        raise web.HTTPRequestEntityTooLarge(max_size=1024 * 1024, actual_size=request.content_length)
    try:
        raw = await request.read()
        verified = verify_trucky_webhook(
            raw, request.headers.get("X-Signature-SHA256"), secret
        )
    except InvalidTruckyWebhook:
        raise web.HTTPUnauthorized(text="Invalid webhook")
    if verified["status"] == "ignored_event":
        return web.json_response({"status": "ignored_event"})
    # HMAC authenticates the sender, but does not prove the delivery's final
    # mileage or ownership. Save only a digest, event name and candidate ID.
    import steam_callback_v2
    pool = request.app.get(steam_callback_v2.POOL_KEY)
    if pool is not None:
        data = verified["data"]
        candidate = data.get("id") if isinstance(data, dict) else None
        if type(candidate) not in (str, int) or not str(candidate).isdecimal() or int(candidate) <= 0:
            candidate = None
        import unified_driver_profiles
        receipt_id = await unified_driver_profiles.record_trucky_webhook_receipt(
            pool, hashlib.sha256(raw).hexdigest(), verified["event"], candidate
        )
        return web.json_response({
            "status": "authenticated_pending_api_verification",
            "receipt_recorded": receipt_id is not None,
            "mileage_awarded": False,
        }, status=202)
    # Isolated schema-capture mode remains stateless.
    return web.json_response({"status": "authenticated_pending_schema_verification"})


def create_staging_app():
    app = web.Application(client_max_size=1024 * 1024)
    app.router.add_post("/staging/trucky/webhook", receive_trucky_webhook)
    return app


def create_integrations_app(pool):
    """Expose health, Steam, and authenticated Trucky routes on bot service."""
    import steam_callback_v2

    async def health(request):
        return web.json_response({"status": "ok"})

    app = web.Application(client_max_size=1024 * 1024)
    app[steam_callback_v2.POOL_KEY] = pool
    app.router.add_get("/healthz", health)
    app.router.add_get("/auth/steam/callback", steam_callback_v2.callback)
    app.router.add_post("/trucky/webhook", receive_trucky_webhook)
    return app


if __name__ == "__main__":
    if os.getenv("AT_V2_ISOLATED_STAGING") != "YES":
        raise SystemExit("Refusing to start outside isolated staging")
    if not os.getenv("TRUCKY_WEBHOOK_SIGNING_SECRET"):
        raise SystemExit("Trucky signing secret is required")
    web.run_app(create_staging_app(), host="127.0.0.1", port=8097)
