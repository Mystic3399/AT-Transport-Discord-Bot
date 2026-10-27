"""Read-only Trucky company job API lookup for independent Discord review.

The Trucky VTC documentation identifies GET /api/v1/company/{id}/jobs.
This adapter does not infer a job schema or award mileage. An authenticated
response is not sufficient until fields and account ownership are validated.
"""
import os
import re
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
                  "finished", "started", "distance", "miles", "kilomet", "classif",
                  "game", "unit", "origin", "destination", "cargo", "truck")

    def visit(value, path=()):
        if isinstance(value, dict):
            for key, item in value.items():
                key_text = str(key)
                dotted = ".".join((*path, key_text))
                normalized = "".join(c for c in key_text.lower() if c.isalnum())
                identity_context = any(
                    any(token in "".join(c for c in part.lower() if c.isalnum())
                        for token in ("driver", "user", "account", "steam", "profile"))
                    for part in path
                )
                relevant_type = (not identity_context and
                    (any(term in normalized for term in safe_terms)
                     or normalized in {"real", "race", "type", "mileagetype", "jobtype", "isreal", "israce"}
                     or normalized.startswith(("realmiles", "racemiles"))))
                if relevant_type or (path == () and normalized in {"id", "driver"}):
                    types[dotted] = type(item).__name__
                classification_field = (
                    normalized in {"real", "race", "type", "mileagetype", "jobtype", "isreal", "israce"}
                    or normalized.startswith(("realmiles", "racemiles"))
                )
                identity_field = any(token in normalized for token in ("driver", "user", "account", "steam", "profile"))
                if (not identity_context and not identity_field
                        and (any(term in normalized for term in safe_terms) or classification_field)
                        and type(item) in (str, int, float, bool)):
                    facts[dotted] = item
                visit(item, (*path, key_text))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, (*path, str(index)))

    visit(record)
    identity = inspect_job_identity_fields(record)
    return {"field_types": types, "safe_delivery_fields": facts,
            "identity_field_presence": identity["safe_report"]}


def inspect_job_identity_fields(record):
    """Inspect driver/Steam field locations while withholding all identifiers."""
    if not isinstance(record, dict):
        raise ValueError("Job record required")
    driver = record.get("driver")
    if not isinstance(driver, dict):
        return {"account_id": None, "steam_id": None, "safe_report": {
            "driver_object_present": False, "driver_account_id_paths": [],
            "steam_identity_paths": [], "valid_steam_identity_present": False,
        }}

    account_keys = {"id", "userid", "accountid", "truckyuserid", "truckyaccountid"}
    account_matches = []
    steam_matches = []

    def visit(value, path=()):
        if isinstance(value, dict):
            for key, item in value.items():
                key_text = str(key)
                normalized = "".join(c for c in key_text.lower() if c.isalnum())
                current = (*path, key_text)
                if not path and normalized in account_keys and type(item) in (str, int) and str(item).isdecimal() and int(item) > 0:
                    account_matches.append((str(item), ".".join(current)))
                if "steam" in normalized and "id" in normalized and type(item) in (str, int):
                    if re.fullmatch(r"7656119[0-9]{10}", str(item)):
                        steam_matches.append((str(item), ".".join(current)))
                visit(item, current)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, (*path, str(index)))

    visit(driver)
    account_ids = {item[0] for item in account_matches}
    steam_ids = {item[0] for item in steam_matches}
    account_id = next(iter(account_ids)) if len(account_ids) == 1 else None
    steam_id = next(iter(steam_ids)) if len(steam_ids) == 1 else None
    return {
        "account_id": account_id,
        "steam_id": steam_id,
        "safe_report": {
            "driver_object_present": True,
            "driver_account_id_paths": sorted({path for _, path in account_matches}),
            "driver_account_id_unambiguous": account_id is not None,
            "steam_identity_paths": sorted({path for _, path in steam_matches}),
            "valid_steam_identity_present": steam_id is not None,
            "steam_identity_unambiguous": len(steam_ids) == 1,
        },
    }



async def ingest_authenticated_completed_jobs(pool, page=1):
    """Connect the authenticated company API to normalized V2 event storage.

    API account IDs must match an active Steam-backed, signed ownership claim
    and an already verified signed receipt for that exact job. This entry point
    persists evidence only; reconciliation/credit activation stays separate.
    No display names, raw payloads or account identifiers are returned/logged.
    """
    payload = await fetch_trucky_jobs_page(page)
    records = payload if isinstance(payload, list) else payload.get('data')
    if not isinstance(records, list):
        raise TruckyJobLookupUnavailable('Unsupported company jobs response')
    return await _ingest_authenticated_job_records(pool, records)


async def _ingest_authenticated_job_records(pool, records):
    """Shared evidence path for live page one and durable historical sweeps."""
    from collections import Counter
    import unified_driver_profiles as profiles
    from trucky_verified_job_candidate_v2 import normalize_verified_trucky_job
    counts = Counter()
    for job in records:
        counts['observed'] += 1
        if (not isinstance(job, dict) or type(job.get('user_id')) is not int
                or type(job.get('id')) is not int or job['user_id']<=0 or job['id']<=0):
            counts['invalid_record'] += 1
            continue
        if os.getenv('AT_V2_PENDING_HISTORY_ENABLED') == 'YES':
            # Retain authenticated completed jobs even if their driver has not
            # registered. No Discord identity or mileage award is inferred.
            if job.get('company_id') == COMPANY_ID and job.get('status') == 'completed':
                from pending_driver_history_v2 import record_pending_history
                retained = {key:job[key] for key in (
                    'game_id','status','stats_type','driven_distance_km',
                    'distance_unit','max_speed','completed_at') if key in job}
                await record_pending_history(pool,'trucky',str(job['id']),str(job['user_id']),
                                             retained,source_kind='authenticated_api')
                counts['history_retained'] += 1
        async with pool.acquire() as conn:
            owners = await conn.fetch("""
                SELECT l.discord_user_id,l.provider_account_id
                FROM unified_provider_links l
                JOIN unified_provider_claims c
                  ON c.provider=l.provider AND c.provider_account_id=l.provider_account_id
                 AND c.discord_user_id=l.discord_user_id
                JOIN unified_steam_links s ON s.discord_user_id=l.discord_user_id
                JOIN unified_driver_profiles p ON p.discord_user_id=l.discord_user_id
                WHERE l.provider='trucky' AND l.provider_account_id=$1
                  AND l.verified_at IS NOT NULL AND s.verified_at IS NOT NULL
                  AND p.status='active' AND c.status='approved'
                  AND c.ownership_confirmed=TRUE AND c.membership_confirmed=TRUE
                  AND c.ownership_evidence_kind='trucky_signed_job_steam_match'
                  AND (c.expires_at IS NULL OR c.expires_at>NOW())
            """, str(job['user_id']))
            receipt_verified = await conn.fetchval("""
                SELECT EXISTS(SELECT 1 FROM unified_trucky_webhook_receipts
                 WHERE candidate_job_id=$1 AND event_type='job_completed'
                   AND review_status IN ('needs_api_verification','verified')
                   AND identity_verified_at IS NOT NULL
                   AND identity_provider_account_id=$2 AND identity_discord_user_id=$3)
            """, str(job['id']),str(job['user_id']),owners[0]['discord_user_id'] if len(owners)==1 else None)
        if len(owners) != 1:
            counts['ownership_pending'] += 1
            continue
        if receipt_verified is not True:
            counts['signed_receipt_pending'] += 1
            continue
        owner = owners[0]
        candidate = normalize_verified_trucky_job(job,
            verified_provider_account_id=owner['provider_account_id'],
            discord_user_id=owner['discord_user_id'])
        if candidate is None:
            counts['classification_or_completion_pending'] += 1
            continue
        try:
            classification_kwargs=({'classification_evidence':candidate['classification_evidence']}
                if 'classification_evidence' in candidate else {})
            inserted = await profiles.record_normalized_provider_job(pool,'trucky',candidate['provider_job_id'],
                owner['discord_user_id'],candidate['kilometers'],'km',
                provider_account_id=owner['provider_account_id'],mileage_type=candidate['mileage_type'],
                delivered_at=candidate['delivered_at'],**classification_kwargs)
        except ValueError:
            counts['conflicting_event_pending'] += 1
            continue
        async with pool.acquire() as conn:
            transitioned = await conn.execute("""
                UPDATE unified_trucky_webhook_receipts SET review_status='verified'
                WHERE candidate_job_id=$1 AND event_type='job_completed'
                  AND review_status='needs_api_verification' AND identity_verified_at IS NOT NULL
                  AND identity_provider_account_id=$2 AND identity_discord_user_id=$3
            """, candidate['provider_job_id'],owner['provider_account_id'],owner['discord_user_id'])
        counts['receipts_verified'] += int(transitioned.split()[-1])
        counts['recorded' if inserted else 'replayed'] += 1
    return {'status':'evidence_processed','counts':dict(counts),'mileage_awarded':False}


async def sweep_saved_trucky_history(pool):
    """One bounded historical page per poll; never an award or complete-feed claim.

    Page one keeps its existing live poll. A database lock serializes sweep
    workers. Evidence commits before the checkpoint: a crash may replay a page
    but cannot skip it. Failures roll back the checkpoint, not retained evidence.
    Old jobs still require the same signed receipt/ownership checks to normalize.
    """
    if (os.getenv('AT_V2_PENDING_HISTORY_ENABLED')!='YES'
            or os.getenv('AT_V2_ISOLATED_STAGING')=='YES'):
        return {'status':'disabled','mileage_awarded':False}
    import hashlib
    import json
    async with pool.acquire() as conn:
        async with conn.transaction():
            if not await conn.fetchval('SELECT pg_try_advisory_xact_lock(490410027)'):
                return {'status':'worker_busy','mileage_awarded':False}
            await conn.execute("INSERT INTO pending_history_capture_cursor_v2(provider) VALUES('trucky') ON CONFLICT DO NOTHING")
            cursor=await conn.fetchrow("SELECT next_page,previous_page_sha256 FROM pending_history_capture_cursor_v2 WHERE provider='trucky' FOR UPDATE")
            page=cursor['next_page']
            payload=await fetch_trucky_jobs_page(page)
            records=payload if isinstance(payload,list) else payload.get('data') if isinstance(payload,dict) else None
            if not isinstance(records,list):
                raise TruckyJobLookupUnavailable('Unsupported company jobs response')
            if not records:
                await conn.execute("UPDATE pending_history_capture_cursor_v2 SET next_page=2,previous_page_sha256=NULL,last_success_at=NOW(),completed_sweeps=completed_sweeps+1 WHERE provider='trucky'")
                return {'status':'sweep_end_observed','page':page,'feed_complete':False,'mileage_awarded':False}
            digest=hashlib.sha256(json.dumps([r.get('id') if isinstance(r,dict) else None for r in records],separators=(',',':')).encode()).hexdigest()
            if digest==cursor['previous_page_sha256']:
                return {'status':'repeated_page_pending','page':page,'feed_complete':False,'mileage_awarded':False}
            result=await _ingest_authenticated_job_records(pool,records)
            if page==100:
                return {'status':'page_limit_pending','page':page,'counts':result['counts'],'feed_complete':False,'mileage_awarded':False}
            await conn.execute("UPDATE pending_history_capture_cursor_v2 SET next_page=$1,previous_page_sha256=$2,last_success_at=NOW() WHERE provider='trucky'",page+1,digest)
            return {'status':'history_page_processed','page':page,'counts':result['counts'],'feed_complete':False,'mileage_awarded':False}
