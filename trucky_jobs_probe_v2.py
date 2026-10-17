"""One-shot, read-only Trucky API schema diagnostic; staging only."""
import asyncio
import json
import os
from trucky_jobs_api_v2 import fetch_trucky_jobs_page, inspect_jobs_response_shape


async def main():
    if os.getenv("AT_V2_ISOLATED_STAGING") != "YES":
        raise SystemExit("Probe refused: isolated staging flag missing")
    if not os.getenv("TRUCKY_API_TOKEN"):
        raise SystemExit("Probe refused: Trucky token missing")
    print("TRUCKY_PROBE_STARTED", flush=True)
    try:
        payload = await asyncio.wait_for(fetch_trucky_jobs_page(1), timeout=25)
        report = inspect_jobs_response_shape(payload)
        print("TRUCKY_PROBE_RESULT " + json.dumps(report, sort_keys=True), flush=True)
    except Exception as exc:
        # Only emit exception class, not body, URLs, credentials or response data.
        print("TRUCKY_PROBE_FAILED " + type(exc).__name__, flush=True)
        raise SystemExit(2) from None


if __name__ == "__main__":
    asyncio.run(main())
