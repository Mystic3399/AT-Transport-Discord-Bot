"""Isolated aiohttp endpoint for Trucky signed webhook schema diagnostics.

Signed receipts may be stored as minimal metadata when a database pool is
provided. No raw payload, driver identity, role write, or mileage award is
stored or applied. The same receiver can be mounted by the bot's HTTP service.
"""
import hashlib
import os
from aiohttp import web
from trucky_signed_webhook_v2 import (
    verify_trucky_webhook, extract_trucky_identity_evidence,
    InvalidTruckyWebhook,
)

ON_TRUCKY_LINKED_KEY = web.AppKey("on_trucky_linked", object)

_IDENTITY_LOG_OUTCOMES = frozenset({
    "pending_identity_verification",
    "not_active_company_member",
    "identity_linked",
    "identity_linked_role_sync_pending",
    "membership_check_unavailable",
    "identity_not_verified",
    "database_unavailable",
})


_IDENTITY_REASON_CODES = frozenset({
    "provider_steam_evidence_missing_or_invalid",
    "claim_missing", "claim_missing_or_inactive", "claim_account_mismatch",
    "steam_link_missing", "steam_identity_mismatch", "receipt_missing",
    "receipt_digest_invalid", "job_evidence_missing", "untrusted_evidence",
    "invalid_account_id", "completion_time_invalid", "approved_link_inconsistent",
    "job_outside_claim_window", "different_account_already_linked",
    "account_owned_by_another_driver", "identity_verification_rejected",
})


def _log_trucky_identity_outcome(status, *, receipt_recorded, active_member=False,
                                 identity_reason=None):
    """Emit only an allowlisted outcome; never log provider or Steam identifiers."""
    outcome = status if status in _IDENTITY_LOG_OUTCOMES else "identity_not_verified"
    ownership_verified = outcome in {
        "identity_linked", "identity_linked_role_sync_pending"
    }
    print(
        "TRUCKY_IDENTITY_VERIFICATION "
        "event=job_completed "
        f"outcome={outcome} "
        f"receipt_recorded={'yes' if receipt_recorded else 'no'} "
        f"active_member={'yes' if active_member else 'no'} "
        f"steam_match={'yes' if ownership_verified else 'no' if identity_reason == 'steam_identity_mismatch' else 'unknown'} "
        f"identity_reason={identity_reason if identity_reason in _IDENTITY_REASON_CODES else 'none'} "
        "mileage_awarded=no",
        flush=True,
    )


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
    if pool is None:
        _log_trucky_identity_outcome(
            "database_unavailable", receipt_recorded=False
        )
        return web.json_response(
            {"status": "authenticated_pending_schema_verification"}
        )
    if pool is not None:
        data = verified["data"]
        candidate = data.get("id") if isinstance(data, dict) else None
        if type(candidate) not in (str, int) or not str(candidate).isdecimal() or int(candidate) <= 0:
            candidate = None
        import unified_driver_profiles
        evidence = None
        try:
            evidence = extract_trucky_identity_evidence(data)
            candidate = evidence["provider_job_id"]
        except InvalidTruckyWebhook:
            # Receipt is retained for audit, but malformed identity data can
            # never approve a link or reach a mileage award path.
            pass
        receipt_id = await unified_driver_profiles.record_trucky_webhook_receipt(
            pool, hashlib.sha256(raw).hexdigest(), verified["event"], candidate
        )
        link_status = "pending_identity_verification"
        identity_reason = None
        active_member = False
        if evidence is not None:
            import trucky_membership_adapter
            try:
                canonical_name = await trucky_membership_adapter.verify_member(
                    evidence["trucky_account_id"]
                )
                if canonical_name is None:
                    link_status = "not_active_company_member"
                else:
                    active_member = True
                    discord_user_id = await unified_driver_profiles.auto_approve_trucky_if_steam_matches(
                        pool,
                        discord_user_id=None,
                        account_id=evidence["trucky_account_id"],
                        provider_steam_id=evidence["steam_id64"],
                        trusted_provider_source=True,
                        membership_confirmed=True,
                        provider_job_id=evidence["provider_job_id"],
                        payload_sha256=hashlib.sha256(raw).hexdigest(),
                        completed_at=evidence["completed_at"],
                    )
                    link_status = "identity_linked"
                    callback = request.app.get(ON_TRUCKY_LINKED_KEY)
                    if callback is not None:
                        try:
                            await callback(discord_user_id)
                        except Exception as role_error:
                            print(
                                "TRUCKY LINK ROLE SYNC FAILED: "
                                f"{type(role_error).__name__}", flush=True
                            )
                            link_status = "identity_linked_role_sync_pending"
            except trucky_membership_adapter.MembershipUnavailable:
                link_status = "membership_check_unavailable"
            except ValueError as verification_error:
                # Return only a bounded reason code; provider and Steam IDs are
                # deliberately excluded from both logs and HTTP responses.
                link_status = "identity_not_verified"
                identity_reason = getattr(
                    verification_error, "code", "identity_verification_rejected"
                )
        else:
            identity_reason = "provider_steam_evidence_missing_or_invalid"
        _log_trucky_identity_outcome(
            link_status,
            receipt_recorded=receipt_id is not None,
            active_member=active_member,
            identity_reason=identity_reason,
        )
        return web.json_response({
            "status": "authenticated_pending_api_verification",
            "receipt_recorded": receipt_id is not None,
            "mileage_awarded": False,
            "identity_status": link_status,
            "identity_reason": identity_reason if identity_reason in _IDENTITY_REASON_CODES else None,
        }, status=202)
    # Isolated schema-capture mode remains stateless.
    return web.json_response({"status": "authenticated_pending_schema_verification"})


def create_staging_app():
    app = web.Application(client_max_size=1024 * 1024)
    app.router.add_post("/staging/trucky/webhook", receive_trucky_webhook)
    return app


def create_integrations_app(pool, on_trucky_linked=None):
    """Expose health, Steam, and authenticated Trucky routes on bot service."""
    import steam_callback_v2

    async def health(request):
        return web.json_response({"status": "ok"})

    app = web.Application(client_max_size=1024 * 1024)
    app[steam_callback_v2.POOL_KEY] = pool
    if on_trucky_linked is not None:
        app[ON_TRUCKY_LINKED_KEY] = on_trucky_linked
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
