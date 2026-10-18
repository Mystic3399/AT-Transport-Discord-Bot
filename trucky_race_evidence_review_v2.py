"""Conservative Trucky Race Miles evidence classifier.

This is NOT a Trucky API parser. Callers must independently authenticate the
provider response and verify account ownership before supplying evidence.
No mileage credit or Company Mods unlock can originate from this module.
"""
from decimal import Decimal, InvalidOperation
from world_of_trucks_policy_v2 import classify_event_mileage


def review_trucky_race_evidence(*, account_verified, company_verified,
                               job_completed, statistics, distance_km,
                               provider_job_id):
    result = {"status": "needs_provider_api_verification",
              "awardable": False, "company_mods_unlock_allowed": False}
    if account_verified is not True or company_verified is not True or job_completed is not True:
        return {**result, "status": "rejected"}
    classification = classify_event_mileage("trucky", statistics)
    if classification is None or classification.mileage_type != "race":
        return {**result, "status": "rejected"}
    if not isinstance(provider_job_id, str) or not provider_job_id.isdecimal() or int(provider_job_id) <= 0:
        return {**result, "status": "rejected"}
    try:
        kilometers = Decimal(str(distance_km))
    except (InvalidOperation, ValueError, TypeError):
        return {**result, "status": "rejected"}
    if not kilometers.is_finite() or not Decimal("0") < kilometers <= Decimal("100000"):
        return {**result, "status": "rejected"}
    return {**result, "provider_job_id": provider_job_id,
            "kilometers": kilometers, "world_of_trucks": classification.world_of_trucks}
