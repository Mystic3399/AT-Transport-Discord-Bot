"""Redacted, staging-only receipts for observed TrucksBook Discord deliveries.

This stores source provenance and a payload digest only. It does not establish
TrucksBook driver ownership, normalize a provider job, or write mileage.
"""
import hashlib
import json
import os
import re
from decimal import Decimal, InvalidOperation


SCHEMA = """
CREATE TABLE IF NOT EXISTS staging_shadow_receipts (
    source_delivery_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL CHECK (provider = 'trucksbook'),
    source_webhook_id BIGINT NOT NULL CHECK (source_webhook_id > 0),
    source_payload_sha256 CHAR(64) NOT NULL,
    provider_job_id TEXT,
    mileage_classification TEXT NOT NULL
        CHECK (mileage_classification IN ('real', 'non_real', 'unknown')),
    candidate_distance_miles NUMERIC(12,3),
    receipt_status TEXT NOT NULL
        CHECK (receipt_status IN ('quarantined', 'malformed')),
    source_authentication TEXT NOT NULL
        CHECK (source_authentication = 'configured_discord_webhook_id_match'),
    ownership_status TEXT NOT NULL DEFAULT 'unverified'
        CHECK (ownership_status = 'unverified'),
    mileage_awarded BOOLEAN NOT NULL DEFAULT FALSE CHECK (mileage_awarded = FALSE),
    registration_changed BOOLEAN NOT NULL DEFAULT FALSE CHECK (registration_changed = FALSE),
    roles_changed BOOLEAN NOT NULL DEFAULT FALSE CHECK (roles_changed = FALSE),
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivery_count INTEGER NOT NULL DEFAULT 1 CHECK (delivery_count > 0)
)
"""


async def install_schema(pool):
    _require_staging()
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_staging_shadow_receipts_job
            ON staging_shadow_receipts(provider, provider_job_id)
        """)


async def audit_receipts(pool, provider_job_id=None):
    """Return a redacted, read-only summary of staging shadow receipts."""
    _require_staging()
    if provider_job_id is not None:
        if (not isinstance(provider_job_id, str)
                or not re.fullmatch(r"[0-9]{1,32}", provider_job_id)):
            raise ValueError("Invalid TrucksBook job ID")

    async with pool.acquire() as conn:
        summary = await conn.fetchrow("""
            SELECT COUNT(*)::BIGINT AS receipt_count,
                   COUNT(DISTINCT provider_job_id)::BIGINT AS unique_jobs,
                   COALESCE(SUM(delivery_count - 1), 0)::BIGINT AS replay_count,
                   MAX(last_seen_at) AS latest_receipt_at,
                   COUNT(*) FILTER (WHERE source_authentication =
                       'configured_discord_webhook_id_match')::BIGINT AS source_verified,
                   COUNT(*) FILTER (WHERE NOT mileage_awarded
                       AND NOT registration_changed AND NOT roles_changed)::BIGINT
                       AS safety_intact,
                   COUNT(*) FILTER (WHERE mileage_awarded OR registration_changed
                       OR roles_changed)::BIGINT AS safety_violations
              FROM staging_shadow_receipts
             WHERE provider = 'trucksbook'
        """)
        match = None
        if provider_job_id is not None:
            match = await conn.fetchrow("""
                SELECT provider_job_id,
                       COUNT(*)::BIGINT AS receipt_count,
                       COALESCE(SUM(delivery_count - 1), 0)::BIGINT AS replay_count,
                       MIN(received_at) AS first_received_at,
                       MAX(last_seen_at) AS latest_receipt_at,
                       COUNT(*) FILTER (WHERE source_authentication =
                           'configured_discord_webhook_id_match')::BIGINT AS source_verified,
                       COUNT(*) FILTER (WHERE NOT mileage_awarded
                           AND NOT registration_changed AND NOT roles_changed)::BIGINT
                           AS safety_intact,
                       COUNT(*) FILTER (WHERE mileage_awarded OR registration_changed
                           OR roles_changed)::BIGINT AS safety_violations
                  FROM staging_shadow_receipts
                 WHERE provider = 'trucksbook' AND provider_job_id = $1
                 GROUP BY provider_job_id
            """, provider_job_id)

    return {
        "receipt_count": int(summary["receipt_count"] or 0),
        "unique_jobs": int(summary["unique_jobs"] or 0),
        "replay_count": int(summary["replay_count"] or 0),
        "latest_receipt_at": summary["latest_receipt_at"],
        "source_verified": int(summary["source_verified"] or 0),
        "safety_intact": int(summary["safety_intact"] or 0),
        "safety_violations": int(summary["safety_violations"] or 0),
        "job": None if match is None else {
            "provider_job_id": match["provider_job_id"],
            "receipt_count": int(match["receipt_count"] or 0),
            "replay_count": int(match["replay_count"] or 0),
            "first_received_at": match["first_received_at"],
            "latest_receipt_at": match["latest_receipt_at"],
            "source_verified": int(match["source_verified"] or 0),
            "safety_intact": int(match["safety_intact"] or 0),
            "safety_violations": int(match["safety_violations"] or 0),
        },
    }


def _require_staging():
    if os.getenv("AT_V2_ISOLATED_STAGING") != "YES":
        raise RuntimeError("Shadow receipts are restricted to isolated staging")


def _payload_digest(payload):
    try:
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("Webhook payload cannot be canonicalized") from error
    return hashlib.sha256(encoded).hexdigest()


async def record_trucksbook_shadow_receipt(
    pool, *, message_id, webhook_id, configured_webhook_id, payload,
    parsed_job=None,
):
    """Persist one authenticated-source observation without personal/job text."""
    _require_staging()
    if type(message_id) is not int or message_id <= 0:
        raise ValueError("Invalid Discord delivery ID")
    if type(webhook_id) is not int or webhook_id <= 0:
        raise ValueError("Invalid Discord webhook ID")
    if (not isinstance(configured_webhook_id, str)
            or not configured_webhook_id.isdecimal()
            or int(configured_webhook_id) != webhook_id):
        raise ValueError("TrucksBook webhook source is not configured")

    job_id = None
    classification = "unknown"
    distance = None
    status = "malformed"
    if parsed_job is not None:
        candidate_id = parsed_job.get("job_id")
        if not isinstance(candidate_id, str) or not re.fullmatch(r"[0-9]+", candidate_id):
            raise ValueError("Invalid candidate provider job ID")
        job_id = candidate_id
        stats = parsed_job.get("statistics")
        if isinstance(stats, str):
            labels = sorted(label.strip().casefold() for label in stats.split(","))
            if labels in (["real"], ["real", "wotr"]):
                classification = "real"
            elif labels:
                classification = "non_real"
        raw_distance = parsed_job.get("accepted_distance")
        if raw_distance is not None:
            try:
                distance = Decimal(str(raw_distance))
            except (InvalidOperation, ValueError) as error:
                raise ValueError("Invalid candidate mileage") from error
            if not distance.is_finite() or distance < 0 or distance > Decimal("10000"):
                raise ValueError("Invalid candidate mileage")
        status = "quarantined"

    digest = _payload_digest(payload)
    async with pool.acquire() as conn:
        async with conn.transaction():
            inserted = await conn.fetchrow("""
                INSERT INTO staging_shadow_receipts
                    (source_delivery_id, provider, source_webhook_id, source_payload_sha256,
                     provider_job_id, mileage_classification,
                     candidate_distance_miles, receipt_status,
                     source_authentication)
                VALUES ($1, 'trucksbook', $2, $3, $4, $5, $6, $7,
                        'configured_discord_webhook_id_match')
                ON CONFLICT(source_delivery_id) DO NOTHING
                RETURNING source_delivery_id, delivery_count, receipt_status
            """, str(message_id), webhook_id, digest, job_id, classification,
                 distance, status)
            if inserted is not None:
                return {"status": inserted["receipt_status"], "duplicate": False,
                        "delivery_count": inserted["delivery_count"]}

            existing = await conn.fetchrow("""
                SELECT source_payload_sha256, receipt_status FROM staging_shadow_receipts
                WHERE source_delivery_id=$1 FOR UPDATE
            """, str(message_id))
            if existing is None or existing["source_payload_sha256"].strip() != digest:
                raise ValueError("Discord delivery ID was reused with different content")
            updated = await conn.fetchrow("""
                UPDATE staging_shadow_receipts
                   SET last_seen_at=NOW(), delivery_count=delivery_count+1
                 WHERE source_delivery_id=$1
                RETURNING delivery_count, receipt_status
            """, str(message_id))
            return {"status": updated["receipt_status"], "duplicate": True,
                    "delivery_count": updated["delivery_count"]}
