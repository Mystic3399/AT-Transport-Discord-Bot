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
    """Safe schema diagnostic: expose keys/types only, not private job values."""
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
    return {
        "outer_keys": outer_keys,
        "record_count": len(records),
        "first_record_types": {
            str(key): type(value).__name__ for key, value in sample.items()
        } if isinstance(sample, dict) else {},
        "verified_delivery": False,
    }
