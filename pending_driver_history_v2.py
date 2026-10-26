"""Provider-scoped pre-registration evidence, never a mileage balance.

Different payload versions remain separate; retries increment a replay count.
Only authenticated stable accounts can be matched to a verified linked driver.
Owner-export names are review labels, not ownership proof.
"""
import hashlib
import json

SCHEMA='''
CREATE TABLE IF NOT EXISTS pending_provider_job_history_v2 (
 provider TEXT NOT NULL CHECK(provider IN ('trucky','trucksbook')),
 provider_job_id TEXT NOT NULL CHECK(provider_job_id ~ '^[0-9]+$'),
 payload_sha256 TEXT NOT NULL CHECK(payload_sha256 ~ '^[0-9a-f]{64}$'),
 provider_account_id TEXT,
 source_kind TEXT NOT NULL CHECK(source_kind IN ('authenticated_api','owner_export')),
 evidence JSONB NOT NULL,
 first_received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 last_received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 replay_count BIGINT NOT NULL DEFAULT 0,
 PRIMARY KEY(provider,provider_job_id,payload_sha256)
);
CREATE INDEX IF NOT EXISTS pending_history_account_idx
 ON pending_provider_job_history_v2(provider,provider_account_id);
'''

FIELDS={'game','game_id','status','stats_type','driven_distance_km','distance_unit','max_speed',
        'completed_at','origin','destination','cargo','distance','unit','max_speed_unit',
        'source_completion_local','candidate_driver_label','company_policy_classification'}

async def install_schema(pool):
    async with pool.acquire() as conn:await conn.execute(SCHEMA)

async def record_pending_history(pool,provider,job_id,account_id,evidence,*,source_kind):
    if provider not in ('trucky','trucksbook') or source_kind not in ('authenticated_api','owner_export'):
        raise ValueError('Unsupported history source')
    if not isinstance(job_id,str) or not job_id.isascii() or not job_id.isdecimal() or int(job_id)<=0:
        raise ValueError('Invalid job reference')
    if account_id is not None and (not isinstance(account_id,str) or not account_id.isascii() or not account_id.isdecimal() or int(account_id)<=0):
        raise ValueError('Invalid provider account')
    if source_kind=='authenticated_api' and account_id is None:
        raise ValueError('Authenticated history requires a stable provider account')
    if not isinstance(evidence,dict) or not evidence or not set(evidence).issubset(FIELDS):
        raise ValueError('Unsupported history evidence fields')
    # Scalar values only: no nested credentials or raw provider payloads.
    if any(value is not None and type(value) not in (str,int,float,bool) for value in evidence.values()):
        raise ValueError('History evidence must contain scalar values')
    payload=json.dumps({'account':account_id,'source_kind':source_kind,'evidence':evidence},sort_keys=True,separators=(',',':'),allow_nan=False)
    digest=hashlib.sha256(payload.encode()).hexdigest()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute('''INSERT INTO pending_provider_job_history_v2
                (provider,provider_job_id,payload_sha256,provider_account_id,source_kind,evidence)
                VALUES($1,$2,$3,$4,$5,$6::jsonb)
                ON CONFLICT(provider,provider_job_id,payload_sha256) DO UPDATE
                  SET replay_count=pending_provider_job_history_v2.replay_count+1,last_received_at=NOW()
            ''',provider,job_id,digest,account_id,source_kind,json.dumps(evidence,allow_nan=False))
    return {'status':'evidence_retained','mileage_awarded':False}

async def linked_history_summary(pool,discord_user_id):
    """Read-only dynamic association, including records received before registration.

    No migration of balances, claims, or awards happens here. A job with any
    conflicting observation/account version remains pending review.
    """
    if type(discord_user_id) is not int or discord_user_id<=0:raise ValueError('Invalid driver')
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            row=await conn.fetchrow('''
                SELECT COUNT(DISTINCT (h.provider,h.provider_job_id)) AS jobs,
                       COALESCE(SUM(h.replay_count),0) AS replays
                FROM pending_provider_job_history_v2 h
                JOIN unified_provider_links l ON l.provider=h.provider
                  AND l.provider_account_id=h.provider_account_id
                  AND l.verified_at IS NOT NULL
                JOIN unified_steam_links s ON s.discord_user_id=l.discord_user_id
                  AND s.verified_at IS NOT NULL
                WHERE l.discord_user_id=$1 AND h.source_kind='authenticated_api'
            ''',discord_user_id)
    return {'jobs':int(row['jobs']),'replays':int(row['replays']),
            'status':'pending_mileage_checks','mileage_awarded':False}
