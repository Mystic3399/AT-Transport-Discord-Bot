"""Fail-closed V2 mileage activation and provider-evidence policy.

This gate intentionally cannot award from isolated Railway staging. The
separate test override is accepted only when the database is explicitly marked
disposable, so CI can exercise the existing transactional staging ledger.
"""
import hashlib
import os
import re
from decimal import Decimal, InvalidOperation


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def mileage_engine_enabled(environ=None):
    env = os.environ if environ is None else environ
    # This implementation is deliberately a disposable-database proof. There
    # is no production writer wired to it; an environment flag alone must not
    # turn it into a live mileage engine.
    if env.get("AT_V2_ISOLATED_STAGING") == "YES":
        return False
    return (
        env.get("AT_V2_TEST_DATABASE_DISPOSABLE") == "YES"
        and env.get("AT_V2_MILEAGE_ENGINE_TEST_ENABLED") == "YES"
    )


def verified_job_evidence_reason(incoming):
    """Return a fail-closed reason for incomplete or untrusted job evidence."""
    if not isinstance(incoming, dict):
        return "invalid_record"
    if incoming.get("provider") not in ("trucksbook", "trucky"):
        return "unsupported_provider"
    job_id = incoming.get("provider_job_id")
    account_id = incoming.get("provider_account_id")
    if (not isinstance(job_id, str) or not job_id.isascii()
            or not job_id.isdecimal() or int(job_id) <= 0):
        return "provider_job_id_unverified"
    if (not isinstance(account_id, str) or not account_id.isascii()
            or not account_id.isdecimal() or int(account_id) <= 0):
        return "provider_account_id_unverified"
    if incoming.get("provider") == "trucksbook" and incoming.get("mileage_type") == "race":
        return "trucksbook_race_excluded"
    if type(incoming.get("discord_user_id")) is not int or incoming["discord_user_id"] <= 0:
        return "driver_identity_unverified"
    if incoming.get("provider_authenticated") is not True:
        return "provider_source_unverified"
    if incoming.get("completed") is not True or incoming.get("job_status") != "completed":
        return "job_completion_unverified"
    if incoming.get("ownership_verified") is not True:
        return "provider_ownership_unverified"
    if incoming.get("steam_identity_verified") is not True:
        return "steam_identity_unverified"
    expected_evidence = {
        "trucky": {"trucky_signed_job_steam_match"},
        # Normal TrucksBook login is email/password. Its initial Steam
        # registration does not establish a repeatable ownership assertion.
        # No supported evidence mechanism has yet been verified for V2.
        # Preserve old claims, but never use the retired SSO label for credit.
        "trucksbook": set(),
    }[incoming["provider"]]
    if incoming.get("ownership_evidence_kind") not in expected_evidence:
        return "ownership_evidence_kind_unverified"
    if not SHA256_RE.fullmatch(str(incoming.get("ownership_evidence_sha256") or "")):
        return "ownership_evidence_missing"
    if not SHA256_RE.fullmatch(str(incoming.get("source_evidence_sha256") or "")):
        return "source_evidence_missing"
    if incoming.get("mileage_type") not in ("real", "race"):
        return "mileage_classification_unverified"
    delivered = incoming.get("delivered_at")
    if getattr(delivered, "tzinfo", None) is None or delivered.utcoffset() is None:
        return "completion_time_unverified"
    try:
        miles = Decimal(str(incoming.get("miles")))
    except (InvalidOperation, ValueError, TypeError):
        return "distance_unverified"
    if not miles.is_finite() or miles <= 0:
        return "distance_unverified"
    return None


def feed_evidence_is_complete(*, authenticated, complete, watermark, snapshot_sha256):
    return (
        authenticated is True
        and complete is True
        and getattr(watermark, "tzinfo", None) is not None
        and watermark.utcoffset() is not None
        and isinstance(snapshot_sha256, str)
        and SHA256_RE.fullmatch(snapshot_sha256) is not None
    )


def policy_mileage_buckets(provider, mileage_type, miles):
    """Return exact combined and Company Mods buckets for one accepted job."""
    amount = Decimal(str(miles))
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Mileage must be positive and finite")
    if provider not in ("trucksbook", "trucky") or mileage_type not in ("real", "race"):
        raise ValueError("Invalid provider or mileage class")
    if provider == "trucksbook" and mileage_type == "race":
        raise ValueError("TrucksBook Race Miles are excluded")
    return {
        "combined_real_miles": amount if mileage_type == "real" else Decimal(0),
        "trucky_race_miles": amount if provider == "trucky" and mileage_type == "race" else Decimal(0),
        "company_mods_combined_real_miles": amount if mileage_type == "real" else Decimal(0),
        "company_mods_trucksbook_real_miles": amount if provider == "trucksbook" and mileage_type == "real" else Decimal(0),
        "company_mods_trucky_race_miles": amount if provider == "trucky" and mileage_type == "race" else Decimal(0),
    }


def canonical_credit_key(provider, provider_job_id, *, shared_journey_id=None,
                         shared_journey_evidence_sha256=None):
    """Use a shared key only with explicit evidence; otherwise namespace IDs."""
    if provider not in ("trucksbook", "trucky"):
        raise ValueError("Unsupported provider")
    if not isinstance(provider_job_id, str) or not provider_job_id.isdecimal():
        raise ValueError("Invalid provider job ID")
    if shared_journey_id is None:
        canonical = f"provider:{provider}:{provider_job_id}"
    else:
        if not isinstance(shared_journey_id, str) or not shared_journey_id.strip():
            raise ValueError("Invalid shared journey ID")
        if not isinstance(shared_journey_evidence_sha256, str) or not SHA256_RE.fullmatch(
            shared_journey_evidence_sha256
        ):
            raise ValueError("Verified shared-journey evidence digest required")
        canonical = "shared:" + shared_journey_id
    return "v2:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


