"""Expiring, exact approval snapshots for the existing legacy repair writer.

No unexplained balance is converted into new credit. No account links, Trucky
credits or activation gates are modified here.
"""
import hashlib
import json
import os
import re

SCHEMA='''
CREATE TABLE IF NOT EXISTS mileage_audit_previews_v2 (
 preview_id BIGSERIAL PRIMARY KEY,
 guild_id BIGINT NOT NULL, channel_id BIGINT NOT NULL, reviewer_id BIGINT NOT NULL,
 snapshot JSONB NOT NULL, snapshot_sha256 TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW()+INTERVAL '20 minutes', applied_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS mileage_audit_preview_owner_idx
 ON mileage_audit_previews_v2(guild_id,channel_id,reviewer_id,preview_id DESC);
'''
FIELDS=('job_id','discord_user_id','trucksbook_name','recorded_distance','actual_distance',
        'missing_distance','source_channel_id','source_message_id','repair_kind',
        'source_delivered_at','audited_at','source_webhook_id','source_provider_account_id')

class AuditPreviewError(ValueError):
    pass

def audit_confirmation_guidance(preview):
    if preview['jobs'] == 0:
        return ('No approved additions: CONFIRM will not change any profile mileage. '
                'Stored or held Trucky jobs are not part of this TrucksBook repair batch.')
    return ('Review the driver breakdown below, then run `!mileageaudit CONFIRM` '
            'within 20 minutes in this channel. This applies only the displayed '
            'TrucksBook repairs; held Trucky jobs are not approved by this confirmation.')


async def read_trucky_allocation_audit(pool, discord_user_id=None):
    """Report actual credits and unresolved decisions, never proposed additions."""
    if discord_user_id is not None and (type(discord_user_id) is not int or discord_user_id <= 0):
        raise ValueError('Invalid driver')
    result = dict(credited_real_jobs=0, credited_real_miles='0', credited_race_jobs=0,
                  credited_race_miles='0', held_real_jobs=0, mileage_changed=False)
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True, isolation='repeatable_read'):
            if not await conn.fetchval("SELECT to_regclass('unified_v2_production_credit_ledger') IS NOT NULL"):
                return result
            rows = await conn.fetch("""SELECT mileage_type,COUNT(*) AS jobs,SUM(miles)::text AS miles
                FROM unified_v2_production_credit_ledger WHERE provider='trucky'
                  AND ($1::bigint IS NULL OR discord_user_id=$1) GROUP BY mileage_type""", discord_user_id)
            for row in rows:
                result['credited_'+row['mileage_type']+'_jobs'] = row['jobs']
                result['credited_'+row['mileage_type']+'_miles'] = row['miles']
            if await conn.fetchval("SELECT to_regclass('unified_v2_production_mileage_decisions') IS NOT NULL"):
                result['held_real_jobs'] = await conn.fetchval("""SELECT COUNT(*) FROM (
                    SELECT DISTINCT ON (d.provider_job_id) d.*
                    FROM unified_v2_production_mileage_decisions d WHERE d.provider='trucky'
                      AND ($1::bigint IS NULL OR d.discord_user_id=$1)
                    ORDER BY d.provider_job_id,d.decision_id DESC
                ) latest WHERE decision='pending_reconciliation'
                  AND reason_code='historical_or_trucksbook_overlap'
                  AND NOT EXISTS(SELECT 1 FROM unified_v2_production_credit_ledger l
                    WHERE l.provider='trucky' AND l.provider_job_id=latest.provider_job_id)""", discord_user_id)
    return result

def audit_source_allowed(webhook_id,config=None):
    env=os.environ if config is None else config
    value=env.get('TRUCKSBOOK_HISTORY_WEBHOOK_IDS') or env.get('TRUCKSBOOK_WEBHOOK_ID','')
    ids=[s.strip() for s in value.split(',')] if isinstance(value,str) else []
    return (type(webhook_id) is int and webhook_id>0 and 1<=len(ids)<=20
        and len(ids)==len(set(ids))
        and all(0<len(s)<=20 and s.isascii() and s.isdecimal() and int(s)>0 for s in ids)
        and str(webhook_id) in ids)

def explicit_audit_miles(embed):
    """The integer repair path must not treat km or fractional values as miles."""
    for field in embed.to_dict().get('fields') or []:
        if str(field.get('name','')).strip().lower()!='details':continue
        for line in str(field.get('value','')).splitlines():
            match=re.fullmatch(r'Accepted distance:\s*([0-9]+(?:[ ,][0-9]{3})*)\s*(?:mi|mil|miles)',line.replace('**','').strip(),re.I)
            if match:return int(match[1].replace(',','').replace(' ',''))
    return None

def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),default=str)

def _identity(guild,channel,reviewer):
    if any(type(v) is not int or v<=0 for v in (guild,channel,reviewer)):
        raise AuditPreviewError('An audit requires a server channel and administrator identity.')

def _candidate(row):
    result={k:row[k] for k in FIELDS}
    if (result['missing_distance']<=0 or result['recorded_distance']<0
        or result['actual_distance']-result['recorded_distance']!=result['missing_distance']
        or result['repair_kind'] not in ('missing_real','missing_wotr','distance_correction')):
        raise AuditPreviewError('A repair delta is inconsistent. Run a fresh audit.')
    return result

async def verified_trucksbook_driver(conn, account_id):
    """Resolve a current, verified unified link by its stable provider ID."""
    if not await conn.fetchval("SELECT to_regclass('unified_provider_links') IS NOT NULL"):
        return None
    return await conn.fetchval("""
        SELECT l.discord_user_id
        FROM unified_provider_links l
        JOIN unified_driver_profiles p USING (discord_user_id)
        WHERE l.provider='trucksbook' AND l.provider_account_id=$1
          AND l.verified_at IS NOT NULL AND p.status='active'
          AND NOT EXISTS (
              SELECT 1 FROM driver_links d
              WHERE (d.discord_user_id=l.discord_user_id
                     AND d.trucksbook_user_id::text<>l.provider_account_id)
                 OR (d.discord_user_id<>l.discord_user_id
                     AND d.trucksbook_user_id::text=l.provider_account_id)
          )
        FOR SHARE OF l,p
    """, str(account_id))


async def audit_account_matches(conn, candidate, *, lock=False):
    account = candidate['source_provider_account_id']
    if not isinstance(account, str) or not re.fullmatch(r'[1-9][0-9]{0,18}', account):
        return False
    user = candidate['discord_user_id']
    sql = 'SELECT trucksbook_user_id FROM driver_links WHERE discord_user_id=$1'
    legacy_id = await conn.fetchval(sql + (' FOR UPDATE' if lock else ''), user)
    if legacy_id is not None:
        return str(legacy_id) == account
    verified_owner = await verified_trucksbook_driver(conn, account)
    if verified_owner is not None:
        return verified_owner == user

    # Some older registrations recorded the ID on the completed verification
    # but left it blank on the active legacy link. Require both records.
    if not await conn.fetchval("""
        SELECT to_regclass('driver_verifications') IS NOT NULL
           AND to_regclass('unified_provider_links') IS NOT NULL
           AND to_regclass('unified_driver_profiles') IS NOT NULL
    """):
        return False
    return bool(await conn.fetchval("""
        SELECT TRUE
        FROM driver_links d
        JOIN driver_verifications v USING (discord_user_id)
        WHERE d.discord_user_id=$1 AND d.trucksbook_user_id IS NULL
          AND LEFT(d.trucksbook_name, 18)<>'__unlinked_driver_'
          AND v.status='completed' AND v.completed_at IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM unified_driver_profiles p
              WHERE p.discord_user_id=$1 AND p.status='suspended'
          )
          AND v.trucksbook_user_id::text=$2
          AND NOT EXISTS (
              SELECT 1 FROM driver_links other
              WHERE other.trucksbook_user_id::text=$2
          )
          AND NOT EXISTS (
              SELECT 1 FROM unified_provider_links l
              WHERE l.provider='trucksbook'
                AND ((l.discord_user_id=$1 AND l.provider_account_id<>$2)
                  OR (l.discord_user_id<>$1 AND l.provider_account_id=$2))
          )
        FOR UPDATE OF d, v
    """, user, account))


async def reconciled_repair_delta(conn, candidate):
    """Calculate the net Real credit using the live TrucksBook matching rules."""
    if not await conn.fetchval("SELECT to_regclass('unified_v2_production_credit_ledger') IS NOT NULL"):
        return candidate['missing_distance'], None
    overlap = await conn.fetchval("""SELECT EXISTS(SELECT 1
        FROM unified_v2_production_credit_ledger WHERE discord_user_id=$1
          AND provider='trucky' AND mileage_type='real')""", candidate['discord_user_id'])
    if not overlap:
        return candidate['missing_distance'], None
    if (os.getenv('AT_V2_AUTOMATIC_LINKED_RECONCILIATION') != 'YES'
            or candidate['recorded_distance'] != 0
            or candidate['repair_kind'] not in ('missing_real', 'missing_wotr')):
        return None, 'cross_provider_review_required'
    from automatic_linked_mileage_v2 import reconcile_trucksbook
    delta, hold = await reconcile_trucksbook(
        conn, candidate['job_id'], candidate['discord_user_id'], candidate['actual_distance'],
    )
    return (None, hold) if hold else (int(delta), None)


async def create_mileage_audit_preview(pool,guild_id,channel_id,reviewer_id,job_ids):
    _identity(guild_id,channel_id,reviewer_id)
    async with pool.acquire() as conn:
        async with conn.transaction(isolation='repeatable_read'):
            rows=await conn.fetch('''SELECT * FROM mileage_repair_candidates
                WHERE applied_at IS NULL AND job_id=ANY($1::text[]) ORDER BY discord_user_id, job_id''',sorted(set(job_ids)))
            candidates=[];held=0
            holds={}; additions={}
            # Simulate the entire batch in application order. The savepoint
            # keeps ledger replacements and duplicate decisions out of storage.
            probe = conn.transaction()
            await probe.start()
            try:
                for row in rows:
                    if not audit_source_allowed(row['source_webhook_id']):
                        holds[row['job_id']] = 'unapproved_webhook_source'
                        held += 1
                        continue
                    if not await audit_account_matches(conn, row):
                        holds[row['job_id']] = 'missing_or_mismatched_trucksbook_link'
                        held += 1
                        continue
                    candidate = _candidate(row)
                    delta, hold = await reconciled_repair_delta(conn, candidate)
                    if hold:
                        holds[candidate['job_id']] = hold
                        held += 1
                        continue
                    candidates.append(candidate)
                    additions[candidate['job_id']] = delta
            finally:
                await probe.rollback()
            drivers=[]
            for user in sorted({r['discord_user_id'] for r in candidates}):
                total=int(await conn.fetchval('SELECT real_miles FROM driver_progress WHERE discord_user_id=$1',user) or 0)
                selected=[r for r in candidates if r['discord_user_id']==user]
                delta=sum(additions[r['job_id']] for r in selected)
                drivers.append(dict(discord_user_id=user,current_real_miles=total,addition=delta,
                                    proposed_real_miles=total+delta,jobs=len(selected)))
            snapshot={'candidates':candidates,'drivers':drivers,'additions':additions};raw=_canonical(snapshot)
            digest=hashlib.sha256(raw.encode()).hexdigest()
            preview_id=await conn.fetchval('''INSERT INTO mileage_audit_previews_v2
                (guild_id,channel_id,reviewer_id,snapshot,snapshot_sha256)
                VALUES($1,$2,$3,$4::jsonb,$5) RETURNING preview_id''',guild_id,channel_id,reviewer_id,raw,digest)
    return dict(preview_id=preview_id,drivers=drivers,jobs=len(candidates),
                addition=sum(d['addition'] for d in drivers),held_jobs=held,held_reasons=holds,mileage_awarded=False,
                job_rows=[dict({k:r[k] for k in ('job_id','discord_user_id','recorded_distance','actual_distance','missing_distance')}, net_addition=additions[r['job_id']])
                          for r in candidates])

async def validate_mileage_audit_preview(conn,guild_id,channel_id,reviewer_id,candidates):
    """Inside the repair transaction; never fall back to an older preview."""
    if os.getenv('AT_V2_ISOLATED_STAGING')=='YES':
        raise AuditPreviewError('Mileage confirmation is disabled in isolated staging.')
    if os.getenv('AT_V2_PRODUCTION_MILEAGE_ENABLED')=='YES':
        raise AuditPreviewError('Legacy repair is disabled while the V2 award engine is active.')
    _identity(guild_id,channel_id,reviewer_id)
    row=await conn.fetchrow('''SELECT *,expires_at>NOW() AS valid FROM mileage_audit_previews_v2
        WHERE guild_id=$1 AND channel_id=$2 AND reviewer_id=$3
        ORDER BY preview_id DESC LIMIT 1 FOR UPDATE''',guild_id,channel_id,reviewer_id)
    if row is None or not row['valid'] or row['applied_at'] is not None:
        raise AuditPreviewError('Run !mileageaudit first, review it, then confirm within 20 minutes in this channel.')
    snapshot=json.loads(row['snapshot']) if isinstance(row['snapshot'],str) else row['snapshot']
    if hashlib.sha256(_canonical(snapshot).encode()).hexdigest()!=row['snapshot_sha256']:
        raise AuditPreviewError('The audit snapshot failed its integrity check. Run a fresh audit.')
    expected={r['job_id']:r for r in snapshot['candidates']}
    current={r['job_id']:_candidate(r) for r in candidates if r['job_id'] in expected}
    if _canonical(current)!=_canonical(expected):
        raise AuditPreviewError('The reviewed repairs changed or were already processed. Run !mileageaudit again.')
    for candidate in current.values():
        if (not audit_source_allowed(candidate['source_webhook_id'])
                or not await audit_account_matches(conn, candidate, lock=True)):
            raise AuditPreviewError('A source or driver account link changed. Run a fresh audit.')
    for driver in snapshot['drivers']:
        # Same lock/order as the live Trucky worker. Disabling that worker
        # does not make its existing Real credits safe to duplicate via repair.
        await conn.execute('SELECT pg_advisory_xact_lock($1)',driver['discord_user_id'])
        total=await conn.fetchval('SELECT real_miles FROM driver_progress WHERE discord_user_id=$1 FOR UPDATE',driver['discord_user_id'])
        if int(total or 0)!=driver['current_real_miles']:
            raise AuditPreviewError('A driver balance changed after the preview. Run !mileageaudit again.')
    additions = snapshot.get('additions')
    if not isinstance(additions, dict) or additions.keys() != expected.keys():
        raise AuditPreviewError('Run !mileageaudit again to review the net cross-provider additions.')
    return {'preview_id':row['preview_id'],'job_ids':set(expected),'additions':additions}

async def consume_mileage_audit_preview(conn,preview_id):
    await conn.execute('UPDATE mileage_audit_previews_v2 SET applied_at=NOW() WHERE preview_id=$1 AND applied_at IS NULL',preview_id)
