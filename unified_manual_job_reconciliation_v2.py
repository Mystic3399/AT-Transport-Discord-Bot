"""Staging-only, auditable cross-provider delivery review.

This ledger records human review only. It never writes mileage, canonical job
keys, registrations, roles, achievements, or Company Mods eligibility.
"""
import re


EVIDENCE_KINDS = {
    "provider_shared_identifier",
    "provider_job_records",
    "independent_telemetry",
}
DECISIONS = {"verified_independent", "confirmed_duplicate"}


def _job_id(value):
    if (not isinstance(value, str) or not value.isascii()
            or not value.isdecimal() or int(value) <= 0):
        raise ValueError("Provider job IDs must be positive decimal strings")
    return value


def _reviewer(value):
    if type(value) is not int or value <= 0:
        raise ValueError("A valid reviewer Discord ID is required")
    return value


def _evidence(kind, digest):
    if kind not in EVIDENCE_KINDS:
        raise ValueError("Unsupported review evidence kind")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("Provide the SHA-256 of the reviewed evidence")


def _require_isolated_staging():
    import os
    if os.getenv("AT_V2_ISOLATED_STAGING") != "YES":
        raise RuntimeError("Manual reconciliation review is staging-only")


async def open_potential_duplicate(pool, trucky_job_id, trucksbook_job_id, reviewer_id):
    """Open or return a candidate case; opening makes no ownership assertion."""
    _require_isolated_staging()
    trucky_job_id = _job_id(trucky_job_id)
    trucksbook_job_id = _job_id(trucksbook_job_id)
    reviewer_id = _reviewer(reviewer_id)
    if trucky_job_id == trucksbook_job_id:
        # Provider-scoped IDs can legitimately be equal; retain both namespaces.
        pass
    async with pool.acquire() as conn:
        async with conn.transaction():
            case = await conn.fetchrow("""
                INSERT INTO unified_job_reconciliation_cases
                    (trucky_job_id,trucksbook_job_id,opened_by)
                VALUES($1,$2,$3)
                ON CONFLICT(trucky_job_id,trucksbook_job_id) DO NOTHING
                RETURNING case_id,status
            """, trucky_job_id, trucksbook_job_id, reviewer_id)
            if case is not None:
                await conn.execute("""
                    INSERT INTO unified_job_reconciliation_audit
                        (case_id,decision,reviewer_discord_id)
                    VALUES($1,'potential_duplicate',$2)
                """, case["case_id"], reviewer_id)
                return {"case_id": case["case_id"], "status": case["status"], "created": True}
            case = await conn.fetchrow("""
                SELECT case_id,status FROM unified_job_reconciliation_cases
                WHERE trucky_job_id=$1 AND trucksbook_job_id=$2
                FOR UPDATE
            """, trucky_job_id, trucksbook_job_id)
            return {"case_id": case["case_id"], "status": case["status"], "created": False}


async def resolve_review_case(
    pool, trucky_job_id, trucksbook_job_id, decision, reviewer_id,
    evidence_kind, evidence_sha256,
):
    """Record a terminal manual decision only after both job owners are verified.

    The references must already be in the normalized, provider-scoped event
    table with account IDs that match currently approved provider claims and
    verified links, on one Steam-verified A&T profile. Evidence bytes are never
    stored; the SHA-256 is retained in the append-only review audit.
    """
    _require_isolated_staging()
    trucky_job_id = _job_id(trucky_job_id)
    trucksbook_job_id = _job_id(trucksbook_job_id)
    reviewer_id = _reviewer(reviewer_id)
    if decision not in DECISIONS:
        raise ValueError("Decision must be verified_independent or confirmed_duplicate")
    _evidence(evidence_kind, evidence_sha256)
    if decision == "confirmed_duplicate" and evidence_kind not in (
        "provider_shared_identifier", "independent_telemetry"
    ):
        raise ValueError("Duplicate confirmation requires a shared identifier or independent telemetry evidence")
    if decision == "verified_independent" and evidence_kind not in (
        "provider_job_records", "independent_telemetry"
    ):
        raise ValueError("Independent-job review requires provider records or independent telemetry evidence")

    async with pool.acquire() as conn:
        async with conn.transaction():
            case = await conn.fetchrow("""
                SELECT case_id,status FROM unified_job_reconciliation_cases
                 WHERE trucky_job_id=$1 AND trucksbook_job_id=$2 FOR UPDATE
            """, trucky_job_id, trucksbook_job_id)
            if case is None:
                raise ValueError("Open a potential-duplicate case before resolving it")
            if case["status"] != "potential_duplicate":
                return {"status": "already_resolved", "case_id": case["case_id"]}

            owner = await conn.fetchrow("""
                SELECT t.discord_user_id,
                       t.provider_account_id AS trucky_account_id,
                       b.provider_account_id AS trucksbook_account_id,
                       t.delivered_at AS trucky_delivered_at,
                       b.delivered_at AS trucksbook_delivered_at,
                       t.source_payload AS trucky_source_payload,
                       b.source_payload AS trucksbook_source_payload
                  FROM unified_provider_job_events t
                  JOIN unified_provider_job_events b
                    ON b.provider='trucksbook' AND b.provider_job_id=$2
                   AND b.discord_user_id=t.discord_user_id
                  JOIN unified_provider_links tl
                    ON tl.provider='trucky' AND tl.provider_account_id=t.provider_account_id
                   AND tl.discord_user_id=t.discord_user_id AND tl.verified_at IS NOT NULL
                  JOIN unified_provider_links bl
                    ON bl.provider='trucksbook' AND bl.provider_account_id=b.provider_account_id
                   AND bl.discord_user_id=b.discord_user_id AND bl.verified_at IS NOT NULL
                  JOIN unified_provider_claims tc
                    ON tc.provider='trucky' AND tc.provider_account_id=t.provider_account_id
                   AND tc.discord_user_id=t.discord_user_id AND tc.status='approved'
                   AND tc.membership_confirmed=TRUE AND tc.ownership_confirmed=TRUE
                  JOIN unified_provider_claims bc
                    ON bc.provider='trucksbook' AND bc.provider_account_id=b.provider_account_id
                   AND bc.discord_user_id=b.discord_user_id AND bc.status='approved'
                   AND bc.membership_confirmed=TRUE AND bc.ownership_confirmed=TRUE
                   -- No verified TrucksBook ownership mechanism is available yet.
                   -- Keep source rows and cases intact, but deny terminal review.
                   AND FALSE
                  JOIN unified_steam_links s
                    ON s.discord_user_id=t.discord_user_id AND s.verified_at IS NOT NULL
                 WHERE t.provider='trucky' AND t.provider_job_id=$1
                   AND t.provider_account_id IS NOT NULL
                   AND b.provider_account_id IS NOT NULL
                 FOR UPDATE OF t,b
            """, trucky_job_id, trucksbook_job_id)
            if owner is None:
                raise ValueError("Both job owners must be verified to the same Steam-linked driver")

            import json
            from unified_job_matching_v2 import _normalized_metadata
            for label, timestamp, source_payload in (
                ("Trucky", owner["trucky_delivered_at"], owner["trucky_source_payload"]),
                ("TrucksBook", owner["trucksbook_delivered_at"], owner["trucksbook_source_payload"]),
            ):
                if timestamp is None or timestamp.tzinfo is None:
                    raise ValueError(f"{label} job requires a timezone-aware provider completion timestamp")
                if isinstance(source_payload, str):
                    source_payload = json.loads(source_payload)
                if not isinstance(source_payload, dict):
                    raise ValueError(f"{label} job requires authenticated provider metadata")
                _normalized_metadata(source_payload.get("job_metadata"))

            await conn.execute("""
                UPDATE unified_job_reconciliation_cases
                   SET status=$2,discord_user_id=$3,reviewed_at=NOW()
                 WHERE case_id=$1 AND status='potential_duplicate'
            """, case["case_id"], decision, owner["discord_user_id"])
            await conn.execute("""
                INSERT INTO unified_job_reconciliation_audit
                    (case_id,decision,reviewer_discord_id,evidence_kind,
                     evidence_sha256,discord_user_id)
                VALUES($1,$2,$3,$4,$5,$6)
            """, case["case_id"], decision, reviewer_id,
                 evidence_kind, evidence_sha256, owner["discord_user_id"])
    return {"status": decision, "case_id": case["case_id"]}

