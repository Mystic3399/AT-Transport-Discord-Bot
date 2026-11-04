"""Conservative cross-provider job matching for review, not mileage credit.

Only a verified same-driver, same-game, same-origin/destination, same-cargo
candidate within the configured delivery-time window may be suggested.
A matching suggestion NEVER automatically merges or credits jobs.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

REQUIRED_FIELDS = ("game", "origin", "destination", "cargo")


def _normalized_metadata(metadata):
    if not isinstance(metadata, dict):
        raise ValueError("Trusted job metadata is required")
    normalized = {}
    for key in REQUIRED_FIELDS:
        value = metadata.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Missing trusted job metadata: " + key)
        normalized[key] = " ".join(value.casefold().split())
    # Observed provider labels only, scoped by game. This broadens review
    # candidates without creating account ownership or shared journey proof.
    # Ambiguous/mod-specific aliases (e.g. Tripoli/Tarabulus, Kedmonton) are
    # deliberately left for review with region/provider city-ID evidence.
    aliases = {
        'ets2': {
            'praha':'prague',
            'newcastle upon tyne':'newcastle',
            'newcastle-upon-tyne':'newcastle',
            'зеница (zenica)':'зеница', 'бар (bar)':'бар',
            'ფოთი (poti)':'ფოთი', 'λεμεσός (lemesos)':'λεμεσός',
            'χανιά (chania)':'χανιά', 'λάρνακα (larnaca)':'λάρνακα',
            'тузла (tuzla)':'тузла', 'нови сад (novi sad)':'нови сад',
            'ниш (niš)':'ниш', 'αθήνα (athens)':'αθήνα',
            'ηράκλειο (heraklion)':'ηράκλειο', 'со́фия (sofia)':'со́фия',
            'плевен (pleven)':'плевен', 'луга (luga)':'луга',
        },
        'ats': {'newyork':'new york', 'toledooh':'toledo'},
    }.get(normalized['game'], {})
    for key in ('origin','destination'):
        normalized[key] = aliases.get(normalized[key], normalized[key])
    return normalized


def _utc(dt):
    if not isinstance(dt, datetime) or dt.tzinfo is None:
        raise ValueError("Timezone-aware delivery timestamp required")
    return dt.astimezone(timezone.utc)


def is_candidate_duplicate(first, second, *, max_minutes=30, max_miles_delta="5.00"):
    """Suggest a duplicate only when both records are fully comparable.

    Never match two records from the same provider; provider-job-id deduplication
    handles those independently. Unknown fields fail closed.
    """
    if first["provider"] == second["provider"]:
        return False
    if first["discord_user_id"] != second["discord_user_id"]:
        return False
    if first["mileage_type"] not in ("real", "race"):
        return False
    if first["mileage_type"] != second["mileage_type"]:
        return False
    if _normalized_metadata(first["metadata"]) != _normalized_metadata(second["metadata"]):
        return False
    if abs(_utc(first["delivered_at"]) - _utc(second["delivered_at"])) > timedelta(minutes=max_minutes):
        return False
    a = Decimal(str(first["miles"]))
    b = Decimal(str(second["miles"]))
    if not a.is_finite() or not b.is_finite() or a < 0 or b < 0:
        raise ValueError("Invalid mileage")
    return abs(a - b) <= Decimal(str(max_miles_delta))



def credited_route_overlaps(rows):
    """Return possible matches from same-driver pairs backed by a stored credit."""
    import json
    from decimal import InvalidOperation

    matches = {}
    for row in rows:
        jobs = []
        try:
            for provider in ("trucky", "trucksbook"):
                payload = json.loads(row[f"{provider}_payload"])
                jobs.append({
                    "provider": provider,
                    "discord_user_id": row["discord_user_id"],
                    "mileage_type": payload["mileage_type"],
                    "metadata": payload["job_metadata"],
                    "delivered_at": row[f"{provider}_delivered_at"],
                    "miles": payload["canonical_miles"],
                })
            if jobs[0]["mileage_type"] == "real" and is_candidate_duplicate(*jobs):
                credited = Decimal(str(row["trucksbook_credited_miles"]))
                if credited.is_finite() and credited > 0:
                    trucky_miles = Decimal(str(jobs[0]["miles"]))
                    matches.setdefault(row["trucky_job_id"], {})[row["trucksbook_job_id"]] = max(
                        Decimal(0), trucky_miles - credited
                    )
        except (ValueError, TypeError, KeyError, InvalidOperation):
            continue
    return matches


def unique_overlap_topups(matches):
    """Preview only one-to-one pairs, using actual stored credit as the baseline."""
    from collections import Counter

    trucksbook_matches = Counter(job_id for options in matches.values() for job_id in options)
    return {
        trucky_id: difference
        for trucky_id, options in matches.items()
        if len(options) == 1
        for trucksbook_id, difference in options.items()
        if trucksbook_matches[trucksbook_id] == 1
    }
