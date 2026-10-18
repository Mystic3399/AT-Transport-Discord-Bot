"""Read-only Trucky company job API lookup for independent Discord review.

The Trucky VTC documentation identifies GET /api/v1/company/{id}/jobs.
This adapter does not infer a job schema or award mileage. An authenticated
response is not sufficient until fields and account ownership are validated.
"""
import os
import aiohttp

COMPANY_ID = 49041
JOBS_URL = "https://e.truckyapp.com/api/v1/company/49041/jobs"


class TruckyJobLookupUnavailable(Exception):
    pass


async def fetch_trucky_jobs_page(page=1):
    """Fetch one company job page without leaking token or guessing response shape."""
    if type(page) is not int or not 1 <= page <= 100:
        raise ValueError("Invalid Trucky jobs page")
    token = os.getenv("TRUCKY_API_TOKEN", "").strip()
    if not token:
        raise TruckyJobLookupUnavailable("Trucky company API token not configured")
    headers = {
        "x-access-token": token,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "A&T Transport LTD Command Bot",
    }
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15)
        ) as session:
            async with session.get(
                JOBS_URL, params={"page": page}, headers=headers,
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise TruckyJobLookupUnavailable(
                        "Trucky jobs API returned non-success status"
                    )
                payload = await response.json(content_type="application/json")
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        raise TruckyJobLookupUnavailable("Trucky jobs API lookup failed") from exc
    if not isinstance(payload, (dict, list)):
        raise TruckyJobLookupUnavailable("Unexpected Trucky jobs response")
    return payload


def inspect_jobs_response_shape(payload):
    """Safe schema diagnostic: expose keys/types only, not private job values.

    Nested driver fields matter for proving provider identity. Report their
    names and Python types, but never values (including IDs or display names).
    """
    if isinstance(payload, list):
        records = payload
        outer_keys = []
    elif isinstance(payload, dict):
        outer_keys = sorted(str(key) for key in payload.keys())
        records = payload.get("data")
    else:
        raise ValueError("Invalid API response")
    if not isinstance(records, list):
        return {"outer_keys": outer_keys, "record_count": None,
                "first_record_types": {}, "verified_delivery": False}
    sample = records[0] if records else {}
    nested_types = {}
    if isinstance(sample, dict):
        for field in ("driver", "game"):
            value = sample.get(field)
            if isinstance(value, dict):
                nested_types[field] = {
                    str(key): type(item).__name__
                    for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
                }
            else:
                nested_types[field] = type(value).__name__
    return {
        "outer_keys": outer_keys,
        "record_count": len(records),
        "first_record_types": {
            str(key): type(value).__name__ for key, value in sample.items()
        } if isinstance(sample, dict) else {},
        "first_record_nested_types": nested_types,
        "verified_delivery": False,
    }


def find_job_record(payload, job_id):
    """Find exactly one record for a requested numeric ID without assuming
    unverified response field names. Returns paths and the private record to
    the caller; callers must never serialize the record itself.
    """
    if type(job_id) not in (str, int) or not str(job_id).isdecimal() or int(job_id) <= 0:
        raise ValueError("Invalid Trucky job ID")
    records = payload if isinstance(payload, list) else (
        payload.get("data") if isinstance(payload, dict) else None
    )
    if not isinstance(records, list):
        return {"status": "unsupported_response_shape", "record_count": None,
                "match_count": 0, "record": None, "id_paths": []}

    def matching_paths(value, path=()):
        found = []
        if isinstance(value, dict):
            for key, item in value.items():
                key_text = str(key)
                normalized = "".join(c for c in key_text.lower() if c.isalnum())
                identity_ancestry = any(
                    token in "".join(c for c in part.lower() if c.isalnum())
                    for part in path for token in ("driver", "user", "account", "steam")
                )
                recognized_job_key = normalized in {"id", "jobid", "jobidentifier", "jobreferenceid"}
                job_parent = any(
                    "job" in "".join(c for c in part.lower() if c.isalnum())
                    for part in path
                )
                if ((recognized_job_key and not identity_ancestry or job_parent and normalized == "id")
                        and type(item) in (str, int) and str(item) == str(job_id)):
                    found.append(".".join((*path, key_text)))
                found.extend(matching_paths(item, (*path, key_text)))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                found.extend(matching_paths(item, (*path, str(index))))
        return found

    matches = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        paths = matching_paths(record)
        if paths:
            matches.append((record, paths))
    return {
        "status": "found" if len(matches) == 1 else "not_found" if not matches else "ambiguous",
        "record_count": len(records),
        "match_count": len(matches),
        "record": matches[0][0] if len(matches) == 1 else None,
        "id_paths": matches[0][1] if len(matches) == 1 else [],
    }


def safe_job_record_report(record):
    """Redact identities while exposing verified job facts and schema names.

    Values are emitted only for non-identity fields whose names clearly
    describe delivery status, timestamps, distance/classification, or game.
    The original record is never returned.
    """
    if not isinstance(record, dict):
        raise ValueError("Job record required")
    facts = {}
    types = {}
    safe_terms = ("status", "complete", "deliver", "timestamp", "date", "time",
                  "distance", "miles", "kilomet", "classif", "game")

    def visit(value, path=()):
        if isinstance(value, dict):
            for key, item in value.items():
                key_text = str(key)
                dotted = ".".join((*path, key_text))
                types[dotted] = type(item).__name__
                normalized = "".join(c for c in key_text.lower() if c.isalnum())
                classification_field = (
                    normalized in {"real", "race", "type", "mileagetype", "jobtype"}
                    or normalized.startswith(("realmiles", "racemiles"))
                )
                if (any(term in normalized for term in safe_terms) or classification_field) and type(item) in (str, int, float, bool):
                    facts[dotted] = item
                visit(item, (*path, key_text))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, (*path, str(index)))

    visit(record)
    return {"field_types": types, "safe_delivery_fields": facts}
