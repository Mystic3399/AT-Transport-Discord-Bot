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
            classification = "real" if "real" in stats.casefold() else "non_real"
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
                    (source_delivery_id, source_webhook_id, source_payload_sha256,
                     provider_job_id, mileage_classification,
                     candidate_distance_miles, receipt_status,
                     source_authentication)
                VALUES ($1, $2, $3, $4, $5, $6, $7,
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
