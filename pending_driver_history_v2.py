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
 source_kind TEXT NOT NULL CHECK(source_kind IN ('authenticated_api','owner_export','verified_webhook_source')),
 evidence JSONB NOT NULL,
 first_received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 last_received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 replay_count BIGINT NOT NULL DEFAULT 0,
 PRIMARY KEY(provider,provider_job_id,payload_sha256)
);
ALTER TABLE pending_provider_job_history_v2
 DROP CONSTRAINT IF EXISTS pending_provider_job_history_v2_source_kind_check;
ALTER TABLE pending_provider_job_history_v2 ADD CONSTRAINT pending_provider_job_history_v2_source_kind_check
 CHECK(source_kind IN ('authenticated_api','owner_export','verified_webhook_source'));
CREATE INDEX IF NOT EXISTS pending_history_account_idx
 ON pending_provider_job_history_v2(provider,provider_account_id);
'''

FIELDS={'game','game_id','status','stats_type','driven_distance_km','distance_unit','max_speed',
        'completed_at','origin','destination','cargo','distance','unit','max_speed_unit',
        'source_completion_local','candidate_driver_label','company_policy_classification'}

async def install_schema(pool):
    async with pool.acquire() as conn:await conn.execute(SCHEMA)

async def record_pending_history(pool,provider,job_id,account_id,evidence,*,source_kind):
    if provider not in ('trucky','trucksbook') or source_kind not in ('authenticated_api','owner_export','verified_webhook_source'):
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
                WITH linked AS (
                SELECT h.*,
                       EXISTS(SELECT 1 FROM unified_provider_claims c
                         WHERE c.provider=l.provider AND c.provider_account_id=l.provider_account_id
                           AND c.discord_user_id=l.discord_user_id AND c.status='approved'
                           AND c.ownership_confirmed=TRUE AND c.membership_confirmed=TRUE
                           AND c.ownership_evidence_sha256 IS NOT NULL
                           AND c.provider='trucky'
                           AND c.ownership_evidence_kind='trucky_signed_job_steam_match'
                           AND (c.expires_at IS NULL OR c.expires_at>NOW())) AS ownership_current
                FROM pending_provider_job_history_v2 h
                JOIN unified_provider_links l ON l.provider=h.provider
                  AND l.provider_account_id=h.provider_account_id
                  AND l.verified_at IS NOT NULL
                JOIN unified_steam_links s ON s.discord_user_id=l.discord_user_id
                  AND s.verified_at IS NOT NULL
                WHERE l.discord_user_id=$1 AND h.source_kind='authenticated_api'
                ), jobs AS (
                  SELECT provider,provider_job_id,SUM(replay_count) AS replays,
                         BOOL_AND(ownership_current) AS ownership_current
                  FROM linked GROUP BY provider,provider_job_id
                ), checked AS (
                  SELECT j.*, (SELECT COUNT(*) FROM pending_provider_job_history_v2 v
                    WHERE v.provider=j.provider AND v.provider_job_id=j.provider_job_id
                      AND v.source_kind='authenticated_api')>1 AS conflicting
                  FROM jobs j
                )
                SELECT COUNT(*) AS jobs,COALESCE(SUM(replays),0) AS replays,
                       COUNT(*) FILTER(WHERE ownership_current AND NOT conflicting) AS ownership_verified_jobs,
                       COUNT(*) FILTER(WHERE conflicting) AS conflicting_jobs,
                       COUNT(*) FILTER(WHERE NOT ownership_current AND NOT conflicting) AS ownership_pending_jobs
                FROM checked
            ''',discord_user_id)
    return {'jobs':int(row['jobs']),'replays':int(row['replays']),
            'ownership_verified_jobs':int(row['ownership_verified_jobs']),
            'conflicting_jobs':int(row['conflicting_jobs']),
            'ownership_pending_jobs':int(row['ownership_pending_jobs']),
            'ready_to_award':0,
            'status':'pending_mileage_checks','mileage_awarded':False}


def saved_history_caption(history):
    """Separate account association from mileage approval in the live profile."""
    if not history['jobs']:
        return None
    return (f"Saved provider history: {history['jobs']} job(s); "
            f"{history['ownership_verified_jobs']} with current ownership evidence, "
            f"{history['ownership_pending_jobs']} awaiting ownership checks, "
            f"{history['conflicting_jobs']} with conflicting observations. "
            "All await mileage and duplicate checks; not added to totals.")


async def retain_trucksbook_source_observation(pool, *, webhook_id, guild_id,
                                              expected_guild_id, configured_webhook_id,
                                              parsed_job, driver_label, configured_webhook_ids=""):
    """Keep genuine-source unlinked deliveries, without authenticating their owner.

    The legacy embed parser does not establish units or account ownership. Keep
    the observed number but leave its unit unknown; never turn it into a credit.
    """
    from trucksbook_webhook_auth_v2 import is_authorized_trucksbook_webhook
    # Use the helper's strict configured-ID comparison in every environment.
    # Its legacy production mode accepts arbitrary webhook sources and is not
    # sufficient evidence for this new history store.
    if configured_webhook_ids:
        if not isinstance(configured_webhook_ids,str):
            return {'status':'source_rejected','mileage_awarded':False}
        sources=[source.strip() for source in configured_webhook_ids.split(',')]
        valid=(1<=len(sources)<=20 and len(sources)==len(set(sources))
               and all(len(source)<=20 and source.isascii() and source.isdecimal() and 0<int(source) for source in sources))
        allowed=valid and any(is_authorized_trucksbook_webhook(
            webhook_id,isolated_staging=True,configured_id=source) for source in sources)
    else:
        allowed=is_authorized_trucksbook_webhook(webhook_id,isolated_staging=True,configured_id=configured_webhook_id)
    if (type(guild_id) is not int or type(expected_guild_id) is not int
            or guild_id<=0 or expected_guild_id<=0 or guild_id!=expected_guild_id or not allowed):
        return {'status':'source_rejected','mileage_awarded':False}
    if not isinstance(parsed_job,dict) or not parsed_job.get('job_id'):
        return {'status':'malformed','mileage_awarded':False}
    evidence={'candidate_driver_label':driver_label if isinstance(driver_label,str) else None,
              'stats_type':parsed_job.get('statistics'),
              'distance':parsed_job.get('accepted_distance'),'unit':None}
    return await record_pending_history(pool,'trucksbook',parsed_job['job_id'],None,evidence,
                                        source_kind='verified_webhook_source')
