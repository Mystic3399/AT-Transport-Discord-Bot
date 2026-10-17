"""Convert observed Trucky Discord embeds to quarantined review records.

Never infer driver ownership from a display name. Never mark an observed
Discord webhook message trusted or feed-complete. No database writes.
"""
from datetime import datetime, timezone
from unified_distance_units import normalize_distance


def quarantine_trucky_observation(observed, *, discord_message_id, observed_at):
    if not isinstance(observed, dict) or observed.get("provider") != "trucky":
        raise ValueError("Not a Trucky observation")
    if observed.get("trusted") is not False:
        raise ValueError("Only untrusted observations belong in quarantine")
    job_id = observed.get("provider_job_id")
    if not isinstance(job_id, str) or not job_id.isdecimal() or int(job_id) <= 0:
        raise ValueError("Invalid Trucky job ID")
    if type(discord_message_id) is not int or discord_message_id <= 0:
        raise ValueError("Invalid Discord message ID")
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("Timezone-aware observation time required")
    kind = observed.get("mileage_type")
    if kind not in ("real", "race"):
        raise ValueError("Invalid mileage classification")
    original, unit, miles = normalize_distance(
        observed.get("distance"), observed.get("distance_unit")
    )
    if original <= 0 or miles <= 0:
        raise ValueError("Non-positive distance")
    name = observed.get("display_name_unverified")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Missing unverified display name")
    return {
        "provider": "trucky",
        "provider_job_id": job_id,
        "discord_message_id": discord_message_id,
        "display_name_unverified": name.strip(),
        "mileage_type": kind,
        "miles": str(miles),
        "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
        "discord_user_id": None,
        "provider_account_id": None,
        "delivered_at": None,
        "verified_delivery": False,
        "provider_feed_complete": False,
        "eligible_for_award": False,
        "review_status": "awaiting_provider_verification",
    }
