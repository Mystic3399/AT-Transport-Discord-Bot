"""Verify Trucky's HTTP webhook signature before accepting event data.

The signature is HMAC-SHA256 over the exact raw HTTP request body, per
Trucky's documented X-Signature-SHA256 contract. Verification authenticates
the transport, NOT driver ownership, job mileage, or cross-provider uniqueness.
"""
import hashlib
import hmac
import json


class InvalidTruckyWebhook(ValueError):
    pass


def verify_trucky_webhook(raw_body, signature, secret):
    if not isinstance(raw_body, bytes) or not raw_body or len(raw_body) > 1024 * 1024:
        raise InvalidTruckyWebhook("Invalid raw request body")
    if not isinstance(secret, str) or not secret:
        raise InvalidTruckyWebhook("Missing Trucky signing secret")
    if not isinstance(signature, str):
        raise InvalidTruckyWebhook("Missing signature")
    candidate = signature.strip().lower()
    if len(candidate) != 64 or any(c not in "0123456789abcdef" for c in candidate):
        raise InvalidTruckyWebhook("Malformed signature")
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(candidate, expected):
        raise InvalidTruckyWebhook("Signature mismatch")
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise InvalidTruckyWebhook("Invalid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("event"), str) or not isinstance(payload.get("data"), dict):
        raise InvalidTruckyWebhook("Invalid event envelope")
    if payload["event"] != "job_completed":
        return {"status": "ignored_event", "event": payload["event"]}
    # Do not return the unreviewed job data as a trusted award input.
    return {"status": "authenticated_pending_schema_verification",
            "event": "job_completed", "verified_delivery": False,
            "eligible_for_award": False,
            "data": payload["data"]}
