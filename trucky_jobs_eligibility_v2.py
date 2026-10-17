"""Aggregate read-only eligibility diagnostics for Trucky staging."""
import asyncio
import json
import os
from collections import Counter
from trucky_jobs_api_v2 import fetch_trucky_jobs_page

def summarize(payload):
    jobs = payload if isinstance(payload, list) else payload.get("data", [])
    if not isinstance(jobs, list):
        return {"error": "unexpected_shape"}
    counts = Counter()
    for job in jobs:
        if not isinstance(job, dict):
            counts["invalid"] += 1
            continue
        counts["total"] += 1
        completed = job.get("status") == "completed" and bool(job.get("completed_at"))
        real = job.get("stats_type") == "real_miles"
        distance = job.get("real_driven_distance_km")
        positive = type(distance) in (int, float) and 0 < distance < 100000
        identity = type(job.get("id")) is int and type(job.get("user_id")) is int
        if completed:
            counts["completed"] += 1
        if completed and real:
            counts["completed_real"] += 1
        if completed and real and positive:
            counts["completed_real_positive_km"] += 1
        if completed and real and positive and identity:
            counts["candidates_pending_ownership"] += 1
        if real and not completed:
            counts["real_not_completed"] += 1
    return dict(counts)

async def main():
    if os.getenv("AT_V2_ISOLATED_STAGING") != "YES":
        raise SystemExit("Staging only")
    if not os.getenv("TRUCKY_API_TOKEN"):
        raise SystemExit("Missing credential")
    print("TRUCKY_ELIGIBILITY_STARTED", flush=True)
    try:
        data = await asyncio.wait_for(fetch_trucky_jobs_page(1), timeout=25)
        print("TRUCKY_ELIGIBILITY_RESULT " + json.dumps(summarize(data), sort_keys=True), flush=True)
    except Exception as exc:
        print("TRUCKY_ELIGIBILITY_FAILED " + type(exc).__name__, flush=True)
        raise SystemExit(2) from None

if __name__ == "__main__":
    asyncio.run(main())
