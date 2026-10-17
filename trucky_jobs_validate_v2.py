"""Read-only Trucky completed-job field validation in isolated staging.

Print only aggregate counts, enumerated classifications and field presence.
No job identifiers, driver identifiers, token or raw payload are logged.
"""
import asyncio
import json
import os
from collections import Counter
from trucky_jobs_api_v2 import fetch_trucky_jobs_page


def summarize(payload):
    records = payload if isinstance(payload, list) else payload.get("data", [])
    if not isinstance(records, list):
        return {"error": "unexpected_data_shape"}
    enums = ("status", "stats_type", "game_mode", "distance_unit")
    result = {"record_count": len(records), "enums": {}, "field_presence": {}}
    for key in enums:
        result["enums"][key] = dict(Counter(
            str(record.get(key))[:50] for record in records if isinstance(record, dict)
        ))
    for key in ("id", "user_id", "driver", "driven_distance", "planned_distance",
                "real_driven_distance_km", "completed_at", "started_at", "game",
                "game_id", "company_id"):
        result["field_presence"][key] = sum(
            isinstance(record, dict) and record.get(key) is not None for record in records
        )
    return result


async def main():
    if os.getenv("AT_V2_ISOLATED_STAGING") != "YES":
        raise SystemExit("Staging required")
    if not os.getenv("TRUCKY_API_TOKEN"):
        raise SystemExit("Trucky token missing")
    print("TRUCKY_VALIDATION_STARTED", flush=True)
    try:
        payload = await asyncio.wait_for(fetch_trucky_jobs_page(1), timeout=25)
        print("TRUCKY_VALIDATION_RESULT " + json.dumps(summarize(payload), sort_keys=True), flush=True)
    except Exception as exc:
        print("TRUCKY_VALIDATION_FAILED " + type(exc).__name__, flush=True)
        raise SystemExit(2) from None


if __name__ == "__main__":
    asyncio.run(main())
