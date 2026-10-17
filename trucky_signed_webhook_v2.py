"""Verify Trucky's HTTP webhook signature before accepting event data.

The signature is HMAC-SHA256 over the exact raw HTTP request body, per
Trucky's documented X-Signature-SHA256 contract. Verification authenticates
the transport, NOT driver ownership, job mileage, or cross-provider uniqueness.
"""
import hashlib
import hmac
import json
import re
from datetime import datetime


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


def extract_trucky_identity_evidence(data):
    """Extract the documented job/driver Steam identity from a signed event.

    This is identity evidence only. The caller must already have verified the
    Trucky HMAC, confirm current company membership through the authenticated
    roster API, and compare the Steam ID with the driver's Steam OpenID link.
    """
    if not isinstance(data, dict):
        raise InvalidTruckyWebhook("Invalid completed-job data")
    driver = data.get("driver")
    if not isinstance(driver, dict):
        raise InvalidTruckyWebhook("Completed-job driver reference missing")

    job_id = data.get("id")
    user_id = driver.get("id")
    if (type(job_id) not in (int, str) or not str(job_id).isdecimal()
            or int(job_id) <= 0):
        raise InvalidTruckyWebhook("Completed-job ID missing")
    if (type(user_id) not in (int, str) or not str(user_id).isdecimal()
            or int(user_id) <= 0):
        raise InvalidTruckyWebhook("Trucky driver ID missing")
    if data.get("user_id") is not None and str(data["user_id"]) != str(user_id):
        raise InvalidTruckyWebhook("Trucky driver ID is ambiguous")

    profile = driver.get("steam_profile")
    if not isinstance(profile, dict):
        raise InvalidTruckyWebhook("Trucky Steam profile reference missing")
    steam_id = profile.get("steam_id")
    if type(steam_id) not in (str, int) or not re.fullmatch(r"7656119[0-9]{10}", str(steam_id)):
        raise InvalidTruckyWebhook("Trucky Steam identity is invalid")

    completed_at = data.get("completed_at")
    if not isinstance(completed_at, str) or not completed_at.strip():
        raise InvalidTruckyWebhook("Completed-job timestamp missing")
    try:
        timestamp = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidTruckyWebhook("Completed-job timestamp invalid") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise InvalidTruckyWebhook("Completed-job timestamp is not timezone-aware")
    return {
        "provider_job_id": str(job_id),
        "trucky_account_id": str(user_id),
        "steam_id64": str(steam_id),
        "completed_at": timestamp,
    }
