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

async def create_mileage_audit_preview(pool,guild_id,channel_id,reviewer_id,job_ids):
    _identity(guild_id,channel_id,reviewer_id)
    async with pool.acquire() as conn:
        async with conn.transaction(isolation='repeatable_read'):
            rows=await conn.fetch('''SELECT * FROM mileage_repair_candidates
                WHERE applied_at IS NULL AND job_id=ANY($1::text[]) ORDER BY job_id''',sorted(set(job_ids)))
            candidates=[];held=0
            for row in rows:
                link=await conn.fetchval('SELECT trucksbook_user_id FROM driver_links WHERE discord_user_id=$1',row['discord_user_id'])
                if (not audit_source_allowed(row['source_webhook_id']) or link is None
                    or str(link)!=row['source_provider_account_id']):
                    held+=1;continue
                candidates.append(_candidate(row))
            drivers=[]
            for user in sorted({r['discord_user_id'] for r in candidates}):
                total=int(await conn.fetchval('SELECT real_miles FROM driver_progress WHERE discord_user_id=$1',user) or 0)
                selected=[r for r in candidates if r['discord_user_id']==user]
                delta=sum(r['missing_distance'] for r in selected)
                drivers.append(dict(discord_user_id=user,current_real_miles=total,addition=delta,
                                    proposed_real_miles=total+delta,jobs=len(selected)))
            snapshot={'candidates':candidates,'drivers':drivers};raw=_canonical(snapshot)
            digest=hashlib.sha256(raw.encode()).hexdigest()
            preview_id=await conn.fetchval('''INSERT INTO mileage_audit_previews_v2
                (guild_id,channel_id,reviewer_id,snapshot,snapshot_sha256)
                VALUES($1,$2,$3,$4::jsonb,$5) RETURNING preview_id''',guild_id,channel_id,reviewer_id,raw,digest)
    return dict(preview_id=preview_id,drivers=drivers,jobs=len(candidates),
                addition=sum(d['addition'] for d in drivers),held_jobs=held,mileage_awarded=False)

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
        link=await conn.fetchval('SELECT trucksbook_user_id FROM driver_links WHERE discord_user_id=$1 FOR UPDATE',candidate['discord_user_id'])
        if not audit_source_allowed(candidate['source_webhook_id']) or str(link)!=candidate['source_provider_account_id']:
            raise AuditPreviewError('A source or driver account link changed. Run a fresh audit.')
    for driver in snapshot['drivers']:
        total=await conn.fetchval('SELECT real_miles FROM driver_progress WHERE discord_user_id=$1 FOR UPDATE',driver['discord_user_id'])
        if int(total or 0)!=driver['current_real_miles']:
            raise AuditPreviewError('A driver balance changed after the preview. Run !mileageaudit again.')
    return {'preview_id':row['preview_id'],'job_ids':set(expected)}

async def consume_mileage_audit_preview(conn,preview_id):
    await conn.execute('UPDATE mileage_audit_previews_v2 SET applied_at=NOW() WHERE preview_id=$1 AND applied_at IS NULL',preview_id)
