"""Fail-closed, additive V2 mileage ledger adapter for future production use.

This adapter writes only separate V2 ledger/aggregate/decision tables. Its
durable progression outbox is consumed by the separately gated bot role worker.
No legacy mileage totals are written. Every award call
rechecks the production activation boundary and database identity.
"""
from decimal import Decimal, InvalidOperation
import os

from unified_mileage_engine_control_v2 import (
    canonical_credit_key,
    verified_job_evidence_reason,
)
from unified_production_mileage_gate_v2 import (
    production_activation_blockers,
    read_only_database_fingerprint,
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS unified_v2_production_credit_ledger (
 provider TEXT NOT NULL CHECK(provider IN ('trucksbook','trucky')),
 provider_job_id TEXT NOT NULL,
 discord_user_id BIGINT NOT NULL REFERENCES unified_driver_profiles(discord_user_id),
 provider_account_id TEXT NOT NULL,
 mileage_type TEXT NOT NULL CHECK(mileage_type IN ('real','race')),
 miles NUMERIC(24,10) NOT NULL CHECK(miles > 0),
 canonical_credit_key TEXT NOT NULL,
 source_evidence_sha256 TEXT NOT NULL CHECK(source_evidence_sha256 ~ '^[0-9a-f]{64}$'),
 ownership_evidence_sha256 TEXT NOT NULL CHECK(ownership_evidence_sha256 ~ '^[0-9a-f]{64}$'),
 credited_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 PRIMARY KEY(provider,provider_job_id),
 UNIQUE(discord_user_id,canonical_credit_key)
);
CREATE TABLE IF NOT EXISTS unified_v2_production_mileage_totals (
 discord_user_id BIGINT PRIMARY KEY REFERENCES unified_driver_profiles(discord_user_id),
 combined_real_miles NUMERIC(24,10) NOT NULL DEFAULT 0 CHECK(combined_real_miles >= 0),
 trucky_race_miles NUMERIC(24,10) NOT NULL DEFAULT 0 CHECK(trucky_race_miles >= 0),
 trucksbook_real_miles NUMERIC(24,10) NOT NULL DEFAULT 0 CHECK(trucksbook_real_miles >= 0),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS unified_v2_production_mileage_baselines (
 discord_user_id BIGINT PRIMARY KEY,
 trucksbook_real_miles BIGINT NOT NULL CHECK(trucksbook_real_miles >= 0),
 snapshotted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS unified_v2_production_mileage_decisions (
 decision_id BIGSERIAL PRIMARY KEY,
 provider TEXT NOT NULL CHECK(provider IN ('trucksbook','trucky')),
 provider_job_id TEXT NOT NULL,
 discord_user_id BIGINT,
 decision TEXT NOT NULL CHECK(decision IN (
   'credited','duplicate_provider_job','pending_reconciliation',
   'duplicate_canonical_credit','rejected_evidence')),
 reason_code TEXT NOT NULL,
 evidence_sha256 TEXT CHECK(evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE unified_v2_production_mileage_decisions
 ADD COLUMN IF NOT EXISTS progression_delivered_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS unified_v2_prod_decisions_job_idx
 ON unified_v2_production_mileage_decisions(provider,provider_job_id,created_at);
"""


def production_ledger_migration_plan():
    """Return operator-facing migration guarantees; intentionally no DDL here."""
    return {
        "mode": "additive_only",
        "legacy_tables_written": [],
        "tables_created": [
            "unified_v2_production_credit_ledger",
            "unified_v2_production_mileage_totals",
            "unified_v2_production_mileage_baselines",
            "unified_v2_production_mileage_decisions",
        ],
        "rollback": "disable adapter; preserve V2 ledger rows; no DROP or legacy restore",
        "historical_backfill": "one-time additive snapshot from driver_progress; conflicts never overwrite",
        "cutover": "pause and drain legacy TrucksBook writer before taking the baseline snapshot",
        "roles_achievements_company_mods": "not written by this adapter",
    }


async def _activation_blockers_for_connection(conn, config, runtime):
    actual_fingerprint = await read_only_database_fingerprint(conn)
    observed = dict(runtime or {})
    # Railway's injected runtime identity is authoritative. A caller may
    # supply test/preflight evidence, but cannot spoof the deployment.
    for key in ("RAILWAY_ENVIRONMENT_ID", "RAILWAY_SERVICE_ID",
                "RAILWAY_DEPLOYMENT_ID", "RAILWAY_GIT_COMMIT_SHA"):
        observed[key] = os.environ.get(key)
    observed["database_fingerprint"] = actual_fingerprint
    effective_config = dict(os.environ if config is None else config)
    # Process-level staging/disposable switches always win over injected
    # config so production code cannot hide a safety override.
    for key in ("AT_V2_ISOLATED_STAGING", "AT_V2_TEST_DATABASE_DISPOSABLE",
                "AT_V2_MILEAGE_ENGINE_TEST_ENABLED"):
        if os.environ.get(key) == "YES":
            effective_config[key] = "YES"
    return production_activation_blockers(effective_config, observed)


async def install_production_ledger_schema(pool, *, config=None, runtime=None):
    """Install additive tables only after the same strict production preflight."""
    async with pool.acquire() as conn:
        blockers = await _activation_blockers_for_connection(conn, config, runtime)
        if blockers:
            raise RuntimeError("Production ledger migration blocked: " + ",".join(blockers))
        async with conn.transaction():
            legacy_tables = await conn.fetchval("""
                SELECT count(*) FROM information_schema.tables
                WHERE table_schema=current_schema()
                  AND table_name = ANY($1::text[])
            """, ["driver_progress", "processed_jobs"])
            if legacy_tables != 2:
                raise RuntimeError("Required legacy mileage tables are missing; migration refused")
            await conn.execute(SCHEMA)
            # Snapshot, never overwrite: migration retries cannot reset or
            # rewrite legacy mileage; all Discord IDs remain represented even
            # if unified profile backfill has not yet run for every driver.
            await conn.execute("""
                INSERT INTO unified_v2_production_mileage_baselines
                  (discord_user_id,trucksbook_real_miles)
                SELECT discord_user_id,real_miles
                FROM driver_progress
                ON CONFLICT(discord_user_id) DO NOTHING
            """)
            existing = await conn.fetchval("""
                SELECT count(*) FROM information_schema.tables
                WHERE table_schema=current_schema()
                  AND table_name = ANY($1::text[])
            """, [
                "unified_v2_production_credit_ledger",
                "unified_v2_production_mileage_totals",
                "unified_v2_production_mileage_baselines",
                "unified_v2_production_mileage_decisions",
            ])
            if existing != 4:
                raise RuntimeError("Production ledger schema verification failed")


def _decimal_miles(value):
    try:
        miles = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Invalid mileage") from exc
    try:
        over_precision = miles != miles.quantize(Decimal("0.0000000001"))
    except InvalidOperation as exc:
        raise ValueError("Mileage exceeds ledger precision") from exc
    if not miles.is_finite() or miles <= 0 or over_precision:
        raise ValueError("Mileage must be positive and within ledger precision")
    return miles


async def apply_verified_credit(
    pool, job, *, config, runtime, feed_evidence,
    reconciliation_status, reconciliation_evidence_sha256=None,
    reviewer_id=None,
):
    """Atomically credit a fully verified event to the separate V2 ledger.

    `reconciliation_status` must be `no_candidate` with complete authenticated
    feed/watermark evidence, `verified_independent` with a matching audited
    review case, or `confirmed_duplicate` with an audited selected primary.
    Pending and non-primary duplicate records never award mileage.
    This function does not update any legacy totals or trigger external effects.
    """
    from unified_mileage_engine_control_v2 import feed_evidence_is_complete, policy_mileage_buckets

    reason = verified_job_evidence_reason(job)
    if reason:
        return {"status": "rejected_evidence", "reason": reason}
    if reconciliation_status not in (
        "no_candidate", "verified_independent", "pending", "confirmed_duplicate"
    ):
        return {"status": "rejected_evidence", "reason": "reconciliation_status_invalid"}
    pending_reason = None
    try:
        feed_complete = isinstance(feed_evidence, dict) and feed_evidence_is_complete(
            authenticated=feed_evidence.get("authenticated"),
            complete=feed_evidence.get("complete"),
            watermark=feed_evidence.get("watermark"),
            snapshot_sha256=feed_evidence.get("snapshot_sha256"),
        )
    except (TypeError, ValueError):
        feed_complete = False
    if reconciliation_status == "pending":
        pending_reason = reconciliation_status
    elif not feed_complete:
        pending_reason = "provider_feed_incomplete"
    if reconciliation_status in ("verified_independent", "confirmed_duplicate"):
        if (type(reviewer_id) is not int or reviewer_id <= 0
                or not isinstance(reconciliation_evidence_sha256, str)
                or len(reconciliation_evidence_sha256) != 64
                or any(c not in "0123456789abcdef" for c in reconciliation_evidence_sha256)):
            pending_reason = "review_evidence_missing"
    elif reconciliation_evidence_sha256 is None and feed_complete:
        # A complete authenticated feed snapshot is itself the audit evidence.
        reconciliation_evidence_sha256 = feed_evidence["snapshot_sha256"]

    provider = job["provider"]
    provider_job_id = job["provider_job_id"]
    user_id = job["discord_user_id"]
    account_id = job["provider_account_id"]
    amount = _decimal_miles(job["miles"])
    key = job.get("canonical_credit_key")
    if (not isinstance(key, str) or not key.startswith("v2:") or len(key) != 67
            or any(c not in "0123456789abcdef" for c in key[3:])):
        return {"status": "rejected_evidence", "reason": "canonical_credit_key_invalid"}
    if reconciliation_status in ("no_candidate", "verified_independent"):
        if key != canonical_credit_key(provider, provider_job_id):
            return {"status": "pending_reconciliation", "reason": "shared_key_requires_duplicate_review"}
    if reconciliation_status == "confirmed_duplicate":
        primary = job.get("reconciliation_primary") or {}
        companion = job.get("reconciliation_companion") or {}
        if (not isinstance(primary, dict)
                or not isinstance(companion, dict)
                or primary.get("provider") not in ("trucky", "trucksbook")
                or companion.get("provider") not in ("trucky", "trucksbook")
                or companion.get("provider") == provider
                or not primary.get("provider_job_id")
                or not companion.get("provider_job_id")):
            pending_reason = pending_reason or "duplicate_review_pair_missing"
        elif (primary.get("provider") != provider
              or primary.get("provider_job_id") != provider_job_id):
            pending_reason = pending_reason or "duplicate_not_selected_for_credit"
    elif reconciliation_status == "verified_independent":
        companion = job.get("reconciliation_companion") or {}
        if (not isinstance(companion, dict)
                or companion.get("provider") not in ("trucky", "trucksbook")
                or companion.get("provider") == provider
                or not companion.get("provider_job_id")):
            pending_reason = pending_reason or "independent_review_pair_missing"
    ownership_digest = job.get("ownership_evidence_sha256")
    if (not isinstance(ownership_digest, str) or len(ownership_digest) != 64
            or any(c not in "0123456789abcdef" for c in ownership_digest)):
        return {"status": "rejected_evidence", "reason": "ownership_evidence_missing"}
    buckets = policy_mileage_buckets(provider, job["mileage_type"], amount)

    async with pool.acquire() as conn:
        # Bind the approval to the actual database being written, not a caller
        # supplied fingerprint. The following transaction writes only V2 tables.
        blockers = await _activation_blockers_for_connection(conn, config, runtime)
        if blockers:
            return {"status": "activation_blocked", "blockers": blockers}

        async with conn.transaction():
            async def log_decision(decision, reason):
                await conn.execute("""
                    INSERT INTO unified_v2_production_mileage_decisions
                      (provider,provider_job_id,discord_user_id,decision,reason_code,evidence_sha256)
                    VALUES($1,$2,$3,$4,$5,$6)
                """, provider, provider_job_id, user_id, decision, reason,
                     reconciliation_evidence_sha256)

            if pending_reason:
                await log_decision("pending_reconciliation", pending_reason)
                return {"status": "pending_reconciliation", "reason": pending_reason}
            if provider == "trucksbook":
                historical = await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM processed_jobs WHERE job_id=$1)",
                    provider_job_id,
                )
                if historical:
                    await log_decision("pending_reconciliation", "historical_job_overlap")
                    return {"status": "pending_reconciliation", "reason": "historical_job_overlap"}
            if reconciliation_status == "no_candidate":
                unresolved = await conn.fetchval("""
                    SELECT EXISTS(
                      SELECT 1 FROM unified_job_reconciliation_cases
                      WHERE status='potential_duplicate'
                        AND (( $1='trucky' AND trucky_job_id=$2 )
                          OR ( $1='trucksbook' AND trucksbook_job_id=$2 ))
                    )
                """, provider, provider_job_id)
                if unresolved:
                    await log_decision("pending_reconciliation", "open_duplicate_case")
                    return {"status": "pending_reconciliation", "reason": "open_duplicate_case"}
            event = await conn.fetchrow("""
                SELECT discord_user_id,provider_account_id,real_miles,race_miles,source_payload
                FROM unified_provider_job_events
                WHERE provider=$1 AND provider_job_id=$2 FOR UPDATE
            """, provider, provider_job_id)
            if (event is None or event["discord_user_id"] != user_id
                    or event["provider_account_id"] != account_id):
                await log_decision("rejected_evidence", "provider_event_mismatch")
                return {"status": "rejected_evidence", "reason": "provider_event_mismatch"}

            if reconciliation_status in ("verified_independent", "confirmed_duplicate") and not pending_reason:
                companion = job["reconciliation_companion"]
                expected_trucky = provider_job_id if provider == "trucky" else companion["provider_job_id"]
                expected_trucksbook = provider_job_id if provider == "trucksbook" else companion["provider_job_id"]
                case_status = reconciliation_status
                audited_case = await conn.fetchval("""
                    SELECT EXISTS(
                      SELECT 1 FROM unified_job_reconciliation_cases c
                      JOIN unified_job_reconciliation_audit a ON a.case_id=c.case_id
                      WHERE c.trucky_job_id=$1 AND c.trucksbook_job_id=$2
                        AND c.discord_user_id=$3 AND c.status=$4
                        AND a.decision=$4 AND a.reviewer_discord_id=$5
                        AND a.evidence_sha256=$6
                    )
                """, expected_trucky, expected_trucksbook, user_id,
                     case_status, reviewer_id, reconciliation_evidence_sha256)
                other_provider_verified = await conn.fetchval("""
                    SELECT EXISTS(
                      SELECT 1 FROM unified_provider_job_events e
                      JOIN unified_provider_links l
                        ON l.provider=e.provider AND l.provider_account_id=e.provider_account_id
                       AND l.discord_user_id=e.discord_user_id AND l.verified_at IS NOT NULL
                      JOIN unified_provider_claims c
                        ON c.provider=l.provider AND c.provider_account_id=l.provider_account_id
                       AND c.discord_user_id=l.discord_user_id
                       AND c.status='approved' AND c.ownership_confirmed=TRUE
                       AND c.membership_confirmed=TRUE
                       AND c.ownership_evidence_sha256 IS NOT NULL
                       AND l.provider='trucky'
                       AND c.ownership_evidence_kind='trucky_signed_job_steam_match'
                       AND (c.expires_at IS NULL OR c.expires_at>NOW())
                      JOIN unified_steam_links s
                        ON s.discord_user_id=e.discord_user_id AND s.verified_at IS NOT NULL
                      WHERE e.provider=$1 AND e.provider_job_id=$2 AND e.discord_user_id=$3
                    )
                """, companion["provider"], companion["provider_job_id"], user_id)
                if not audited_case or not other_provider_verified:
                    await log_decision("pending_reconciliation", "review_case_or_companion_unverified")
                    return {"status": "pending_reconciliation", "reason": "review_case_or_companion_unverified"}

            ownership = await conn.fetchval("""
                SELECT EXISTS(
                  SELECT 1 FROM unified_provider_links l
                  JOIN unified_provider_claims c
                    ON c.provider=l.provider AND c.provider_account_id=l.provider_account_id
                   AND c.discord_user_id=l.discord_user_id
                  JOIN unified_steam_links s ON s.discord_user_id=l.discord_user_id
                  WHERE l.provider=$1 AND l.provider_account_id=$2
                    AND l.discord_user_id=$3 AND l.verified_at IS NOT NULL
                    AND c.status='approved' AND c.ownership_confirmed=TRUE
                    AND c.membership_confirmed=TRUE
                    AND c.ownership_evidence_sha256=$4
                    AND l.provider='trucky'
                    AND c.ownership_evidence_kind='trucky_signed_job_steam_match'
                    AND (c.expires_at IS NULL OR c.expires_at>NOW())
                    AND s.verified_at IS NOT NULL
                )
            """, provider, account_id, user_id, job["ownership_evidence_sha256"])
            if not ownership:
                await log_decision("rejected_evidence", "provider_ownership_not_linked")
                return {"status": "rejected_evidence", "reason": "provider_ownership_not_linked"}

            expected_real = amount if job["mileage_type"] == "real" else Decimal(0)
            expected_race = amount if job["mileage_type"] == "race" else Decimal(0)
            # Legacy normalized event columns are NUMERIC(12,2). Compare the
            # exact ten-place distance retained in the immutable source payload
            # as well, so the new ledger does not inherit that display precision.
            import json
            source_payload = event["source_payload"]
            if isinstance(source_payload, str):
                source_payload = json.loads(source_payload)
            try:
                exact_event_miles = Decimal(str(source_payload["canonical_miles"]))
            except (TypeError, KeyError, InvalidOperation):
                exact_event_miles = None
            stored_real = Decimal(str(event["real_miles"]))
            stored_race = Decimal(str(event["race_miles"]))
            if (exact_event_miles != amount
                    or source_payload.get("mileage_type") != job["mileage_type"]
                    or (job["mileage_type"] == "real" and (stored_real <= 0 or stored_race != 0))
                    or (job["mileage_type"] == "race" and (stored_race <= 0 or stored_real != 0))):
                await log_decision("rejected_evidence", "distance_or_classification_mismatch")
                return {"status": "rejected_evidence", "reason": "distance_or_classification_mismatch"}

            inserted = await conn.fetchval("""
                INSERT INTO unified_v2_production_credit_ledger
                  (provider,provider_job_id,discord_user_id,provider_account_id,
                   mileage_type,miles,canonical_credit_key,
                   source_evidence_sha256,ownership_evidence_sha256)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)
                ON CONFLICT DO NOTHING RETURNING provider_job_id
            """, provider, provider_job_id, user_id, account_id,
                 job["mileage_type"], amount, job["canonical_credit_key"],
                 job["source_evidence_sha256"], job["ownership_evidence_sha256"])
            if inserted is None:
                existing = await conn.fetchrow("""
                    SELECT discord_user_id,miles,mileage_type,canonical_credit_key
                    FROM unified_v2_production_credit_ledger
                    WHERE provider=$1 AND provider_job_id=$2
                """, provider, provider_job_id)
                if existing is not None:
                    if (existing["discord_user_id"] != user_id
                            or Decimal(str(existing["miles"])) != amount
                            or existing["mileage_type"] != job["mileage_type"]
                            or existing["canonical_credit_key"] != job["canonical_credit_key"]):
                        await log_decision("rejected_evidence", "conflicting_provider_job_replay")
                        return {"status": "rejected_evidence", "reason": "conflicting_provider_job_replay"}
                    decision, reason_code = "duplicate_provider_job", "idempotent_replay"
                else:
                    decision, reason_code = "duplicate_canonical_credit", "canonical_key_already_credited"
                await log_decision(decision, reason_code)
                return {"status": decision, "reason": reason_code}

            await conn.execute("""
                INSERT INTO unified_v2_production_mileage_baselines(discord_user_id,trucksbook_real_miles)
                VALUES($1,COALESCE((SELECT real_miles FROM driver_progress WHERE discord_user_id=$1),0))
                ON CONFLICT(discord_user_id) DO NOTHING
            """, user_id)
            await conn.execute("""
                INSERT INTO unified_v2_production_mileage_totals
                  (discord_user_id,combined_real_miles,trucky_race_miles,trucksbook_real_miles)
                VALUES($1,$2,$3,$4)
                ON CONFLICT(discord_user_id) DO UPDATE SET
                  combined_real_miles=unified_v2_production_mileage_totals.combined_real_miles+EXCLUDED.combined_real_miles,
                  trucky_race_miles=unified_v2_production_mileage_totals.trucky_race_miles+EXCLUDED.trucky_race_miles,
                  trucksbook_real_miles=unified_v2_production_mileage_totals.trucksbook_real_miles+EXCLUDED.trucksbook_real_miles,
                  updated_at=NOW()
            """, user_id, buckets["combined_real_miles"],
                 buckets["trucky_race_miles"], buckets["company_mods_trucksbook_real_miles"])
            await log_decision("credited", "verified_and_reconciled")
            return {"status": "credited", "miles": str(amount), "policy_buckets": buckets}



async def deliver_pending_progression(pool, apply_roles, *, config=None, runtime=None, limit=20):
    """Retry idempotent role sync only after committed credits and full preflight.

    The decision row is the durable outbox: failed Discord writes roll back only
    its delivery marker, never the already committed mileage. SKIP LOCKED lets
    concurrent workers handle different decisions without duplicate delivery.
    The callback must be idempotent and must not send milestone announcements.
    """
    env = os.environ if config is None else config
    if env.get('AT_V2_PROGRESSION_ENABLED') != 'YES':
        return {'status': 'disabled', 'delivered': 0}
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Invalid progression batch size')
    delivered = 0
    async with pool.acquire() as conn:
        blockers = await _activation_blockers_for_connection(conn, env, runtime)
        if blockers:
            return {'status': 'activation_blocked', 'blockers': blockers, 'delivered': 0}
        for _ in range(limit):
            async with conn.transaction():
                row = await conn.fetchrow("""
                    SELECT decision_id,discord_user_id
                    FROM unified_v2_production_mileage_decisions
                    WHERE decision='credited' AND progression_delivered_at IS NULL
                    ORDER BY decision_id FOR UPDATE SKIP LOCKED LIMIT 1
                """)
                if row is None:
                    break
                totals = await conn.fetchrow("""
                    SELECT b.trucksbook_real_miles + COALESCE(t.combined_real_miles,0) AS real_miles,
                           b.trucksbook_real_miles + COALESCE(t.trucksbook_real_miles,0) AS trucksbook_real_miles,
                           COALESCE(t.trucky_race_miles,0) AS trucky_race_miles
                    FROM unified_v2_production_mileage_baselines b
                    LEFT JOIN unified_v2_production_mileage_totals t USING(discord_user_id)
                    WHERE b.discord_user_id=$1
                """, row['discord_user_id'])
                if totals is None:
                    raise RuntimeError('Historical progression baseline missing')
                eligible = (totals['trucksbook_real_miles'] >= Decimal('40000')
                            or totals['trucky_race_miles'] >= Decimal('60000'))
                result = await apply_roles(row['discord_user_id'], totals['real_miles'],
                    eligible and env.get('AT_V2_COMPANY_MODS_ENABLED') == 'YES')
                if not result or result.get('status') not in ('updated', 'correct'):
                    raise RuntimeError('Progression role delivery incomplete')
                await conn.execute("""UPDATE unified_v2_production_mileage_decisions
                    SET progression_delivered_at=NOW() WHERE decision_id=$1""", row['decision_id'])
                delivered += 1
    return {'status': 'processed', 'delivered': delivered}


def read_approved_progression_preflight(environ=None):
    """Load only the owner-approved, checksum-bound local acceptance manifest."""
    import hashlib
    import json
    from pathlib import Path
    env = os.environ if environ is None else environ
    if env.get('AT_V2_PROGRESSION_ENABLED') != 'YES':
        return None
    expected = env.get('AT_V2_APPROVED_PREFLIGHT_SHA256', '')
    if len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
        raise ValueError('Approved preflight digest missing')
    path = env.get('AT_V2_APPROVED_PREFLIGHT_PATH')
    if not path:
        raise ValueError('Approved preflight manifest missing')
    raw = Path(path).read_bytes()
    if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Preflight manifest checksum mismatch')
    facts = json.loads(raw)
    if not isinstance(facts, dict):
        raise ValueError('Invalid preflight manifest')
    facts['preflight_sha256'] = expected
    return facts
