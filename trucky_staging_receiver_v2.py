"""Isolated aiohttp endpoint for Trucky signed webhook schema diagnostics.

No database connections, driver role writes, or mileage awards. Mount only in
an isolated staging process after configuring the Trucky signing secret.
"""
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
    # Never echo raw job data or credentials to clients/logs.
    return web.json_response({"status": "authenticated_pending_schema_verification"})


def create_staging_app():
    app = web.Application(client_max_size=1024 * 1024)
    app.router.add_post("/staging/trucky/webhook", receive_trucky_webhook)
    return app


if __name__ == "__main__":
    if os.getenv("AT_V2_ISOLATED_STAGING") != "YES":
        raise SystemExit("Refusing to start outside isolated staging")
    if not os.getenv("TRUCKY_WEBHOOK_SIGNING_SECRET"):
        raise SystemExit("Trucky signing secret is required")
    web.run_app(create_staging_app(), host="127.0.0.1", port=8097)
