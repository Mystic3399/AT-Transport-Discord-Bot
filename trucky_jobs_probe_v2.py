"""One-shot authenticated Trucky jobs schema probe, for isolated staging only.

Run: AT_V2_ISOLATED_STAGING=YES python -m trucky_jobs_probe_v2
Requires existing TRUCKY_API_TOKEN; prints keys/types only, never values.
"""
import asyncio
import json
import os
from trucky_jobs_api_v2 import fetch_trucky_jobs_page, inspect_jobs_response_shape


async def main():
    if os.getenv("AT_V2_ISOLATED_STAGING") != "YES":
        raise SystemExit("Refusing jobs probe outside isolated staging")
    if not os.getenv("TRUCKY_API_TOKEN"):
        raise SystemExit("Trucky token unavailable in this environment")
    response = await fetch_trucky_jobs_page(1)
    report = inspect_jobs_response_shape(response)
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
