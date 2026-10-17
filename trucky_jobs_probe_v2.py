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
        records = payload if isinstance(payload, list) else payload.get("data", [])
        completed = next((
            job for job in records
            if isinstance(job, dict)
            and job.get("status") == "completed"
            and isinstance(job.get("user_id"), int)
        ), None)
        if completed is not None:
            # Reuse the authenticated company roster lookup; expose only field
            # names/types so this diagnostic cannot leak member identity.
            from trucky_membership_adapter import inspect_member_identity_fields
            try:
                member_report = await asyncio.wait_for(
                    inspect_member_identity_fields(str(completed["user_id"])),
                    timeout=25,
                )
            except Exception as member_error:
                # Membership is independent of jobs. Report a safe failure
                # category without losing the successful job-schema diagnostic.
                member_report = {
                    "found": False,
                    "field_types": {},
                    "steam_id_verified": False,
                    "lookup_error": getattr(
                        member_error, "diagnostic_code", type(member_error).__name__
                    ),
                }
            print("TRUCKY_MEMBER_IDENTITY_SHAPE " +
                  json.dumps(member_report, sort_keys=True), flush=True)
        else:
            print("TRUCKY_MEMBER_IDENTITY_SHAPE {\"found\":false,\"field_types\":{}}",
                  flush=True)
    except Exception as exc:
        # Only emit exception class, not body, URLs, credentials or response data.
        print("TRUCKY_PROBE_FAILED " + type(exc).__name__, flush=True)
        raise SystemExit(2) from None


if __name__ == "__main__":
    asyncio.run(main())
