"""Validate Trucky API job ownership against a verified A&T provider link.

This adapter does not award mileage or trust a user-submitted account ID.
"""
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

from unified_reconciliation_policy_v2 import classify_delivery

TRUCKY_COMPANY_ID = 49041


def normalize_verified_trucky_job(job, *, verified_provider_account_id, discord_user_id):
    """Return a review-only candidate, or None if job cannot be trusted."""
    if not isinstance(job, dict):
        return None
    if type(discord_user_id) is not int or discord_user_id <= 0:
        return None
    if type(job.get("company_id")) is not int or job["company_id"] != TRUCKY_COMPANY_ID:
        return None
    if type(job.get("id")) is not int or job["id"] <= 0:
        return None
    if type(job.get("user_id")) is not int or job["user_id"] <= 0:
        return None
    if not isinstance(verified_provider_account_id, str) or not verified_provider_account_id.isdecimal():
        return None
    if str(job["user_id"]) != verified_provider_account_id:
        return None
    if job.get("status") != "completed" or not isinstance(job.get("completed_at"), str) or not job["completed_at"]:
        return None
    statistics = {'real_miles':'Real','race_miles':'Race'}.get(job.get('stats_type'))
    if statistics is None or job.get("distance_unit") != "km":
        return None
    # Observed authenticated API contract: game 1 = ETS2, game 2 = ATS.
    # A conflicting nested game descriptor is not an acceptable fallback.
    game = {1:'ETS2',2:'ATS'}.get(job.get('game_id')) if type(job.get('game_id')) is int else None
    if game is None:
        return None
    descriptor=job.get('game')
    if descriptor is not None and (not isinstance(descriptor,dict)
            or descriptor.get('code')!=game
            or type(descriptor.get('id')) is not int or descriptor['id']!=job['game_id']):
        return None
    # World of Trucks is an event tag, not a reason to discard Real Miles.
    # Keep this adapter read-only until provider API verification is complete.
    from world_of_trucks_policy_v2 import classify_job_mileage
    event_tag = job.get("event_tag")
    if event_tag not in (None, "WoTr"):
        return None  # Unknown event tags require provider schema review.
    classification = classify_job_mileage('trucky',statistics+(', WoTr' if event_tag=='WoTr' else ''),
        game=game,max_speed=job.get('max_speed_kmh'),speed_unit='km/h')
    if classification is None:
        return None
    try:
        # The authenticated completed-job driven_distance_km matches Trucky
        # tracker distance. real_driven_distance_km is a different metric;
        # selecting it produced 394.57 mi for the verified 364 mi delivery.
        km = Decimal(str(job.get("driven_distance_km")))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not km.is_finite() or km <= 0 or km > 100000:
        return None
    try:
        delivered_at = datetime.fromisoformat(job["completed_at"].replace("Z", "+00:00"))
    except ValueError:
        return None
    if delivered_at.tzinfo is None or delivered_at.astimezone(timezone.utc) > datetime.now(timezone.utc):
        return None
    result = {
        "delivered_at": delivered_at,
        "provider": "trucky",
        "provider_job_id": str(job["id"]),
        "discord_user_id": discord_user_id,
        "mileage_type": classification.mileage_type,
        "kilometers": km,
        "verified_delivery": True,
        "eligible_for_award": False,
        "trusted": False,
    }
    # Field names independently observed in the authenticated company jobs API.
    # Preserve complete comparison metadata; never invent a shared journey ID.
    metadata = {'game':game, 'origin':job.get('source_city_name'),
                'destination':job.get('destination_city_name'), 'cargo':job.get('cargo_name')}
    if all(isinstance(value,str) and value.strip() and len(value)<=300 for value in metadata.values()):
        result['job_metadata'] = metadata
    if game=='ATS' or classification.mileage_type=='race':
        result['classification_evidence']={
            'game':game,'provider_stats_type':job['stats_type'],
            'policy':'ats_maximum_speed_0_81_real_82_112_race' if game=='ATS' else 'ets2_provider_classification',
        }
        if game=='ATS':
            result['classification_evidence']['max_speed_kmh']=str(job['max_speed_kmh'])
    return result



def review_trucky_job_against_other_provider(
    job, *, verified_provider_account_id, discord_user_id,
    other_provider_jobs, provider_watermark=None, provider_feed_complete=False
):
    """Read-only cross-provider decision. Never returns an award instruction.

    A caller cannot establish authenticated feed completeness simply by
    passing flags. The result remains pending even for a creditable policy
    decision until independent feed and ownership verification is implemented.
    """
    candidate = normalize_verified_trucky_job(
        job, verified_provider_account_id=verified_provider_account_id,
        discord_user_id=discord_user_id,
    )
    if candidate is None:
        return {"status": "rejected", "awardable": False}
    decision = classify_delivery(
        candidate, other_provider_jobs,
        provider_watermark=provider_watermark,
        provider_feed_complete=provider_feed_complete,
    )
    return {"status": decision, "awardable": False}

