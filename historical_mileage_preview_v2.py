"""Read-only historical comparison. Export rows are evidence candidates, never credits.

Input records use the existing matcher shape, plus provider_account_id. An
export adapter must supply explicit units and timezone-aware completion times;
no CSV header, timezone, driver identity or feed completeness is inferred here.
"""
import argparse
import asyncio
import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from unified_distance_units import KM_TO_MILES, UNIT_ALIASES
from unified_job_matching_v2 import is_candidate_duplicate
from world_of_trucks_policy_v2 import classify_event_mileage


async def read_historical_snapshot(pool):
    """One consistent snapshot; PostgreSQL itself rejects writes in this scope."""
    async with pool.acquire() as conn:
        async with conn.transaction(isolation='repeatable_read', readonly=True):
            drivers = await conn.fetch('SELECT discord_user_id,real_miles FROM driver_progress ORDER BY discord_user_id')
            jobs = await conn.fetch('SELECT job_id,discord_user_id,accepted_distance,statistics FROM processed_jobs ORDER BY job_id')
            links = await conn.fetch('''SELECT provider,provider_account_id,discord_user_id,
                verified_at IS NOT NULL AS verified FROM unified_provider_links
                ORDER BY provider,provider_account_id''')
            captured = await conn.fetchval('SELECT transaction_timestamp()')
    return {'captured_at':captured.isoformat(), 'drivers':[dict(r) for r in drivers],
            'processed_jobs':[dict(r) for r in jobs], 'links':[dict(r) for r in links]}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(',',':')).encode()).hexdigest()


def _normalize(row, links):
    provider = row.get('provider')
    job_id = row.get('provider_job_id')
    if provider not in ('trucky','trucksbook') or not isinstance(job_id,str) or not job_id.isascii() or not job_id.isdecimal() or int(job_id)<=0:
        raise ValueError('invalid_provider_reference')
    account = row.get('provider_account_id')
    owners = links.get((provider,account), []) if isinstance(account,str) else []
    if len(owners)!=1:
        raise ValueError('missing_or_ambiguous_account_link')
    owner = owners[0]
    user = owner['discord_user_id']
    if type(user) is not int or user<=0:
        raise ValueError('invalid_account_link')
    if row.get('discord_user_id', user)!=user:
        raise ValueError('conflicting_driver_identity')
    classification = classify_event_mileage(provider,row.get('statistics'))
    if classification is None:
        raise ValueError('excluded_or_ambiguous_classification')
    unit = row.get('unit')
    if not isinstance(unit,str) or unit.strip().lower() not in UNIT_ALIASES:
        raise ValueError('distance_unit_missing')
    value = row.get('distance')
    if isinstance(value,bool):
        raise ValueError('invalid_distance')
    try:
        distance = Decimal(str(value))
        if not distance.is_finite() or distance<=0:
            raise ValueError('invalid_distance')
        miles = distance * (KM_TO_MILES if UNIT_ALIASES[unit.strip().lower()]=='km' else Decimal(1))
    except InvalidOperation:
        raise ValueError('invalid_distance') from None
    timestamp = row.get('delivered_at')
    try:
        timestamp = datetime.fromisoformat(timestamp.replace('Z','+00:00')) if isinstance(timestamp,str) else timestamp
        if not isinstance(timestamp,datetime) or timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError('completion_timezone_missing')
    except (TypeError,ValueError):
        raise ValueError('completion_timezone_missing') from None
    return {'provider':provider,'provider_job_id':job_id,'discord_user_id':user,
            'mileage_type':classification.mileage_type,'miles':miles,'delivered_at':timestamp,
            'metadata':row.get('metadata'),'link_verified':owner.get('verified') is True}


def build_historical_preview(snapshot, records):
    """Compare candidate rows without authenticating exports or approving mileage.

    Missing processed-job IDs never mean uncredited: legacy totals may include
    lump-sum imports. Similar jobs stay pending, never merged or summed as new
    mileage. No output from this function is an award authorization.
    """
    if not isinstance(records,list):
        raise ValueError('Expected a list of normalized export records')
    links=defaultdict(list)
    for link in snapshot['links']:
        links[(link['provider'],link['provider_account_id'])].append(link)
    historical={str(j['job_id']):j for j in snapshot['processed_jobs']}
    groups=defaultdict(list)
    for index,row in enumerate(records):
        key=(row.get('provider'),row.get('provider_job_id')) if isinstance(row,dict) else (None,None)
        # Invalid rows remain separate; do not conflate missing IDs.
        if not all(isinstance(k,str) for k in key): key=('invalid',str(index))
        groups[key].append(row)
    rows=[]; normalized=[]
    for group in groups.values():
        row=group[0]
        result={'row_sha256':_digest(row),'replays':len(group)-1,'decision':'pending','mileage_awarded':False}
        rows.append(result)
        if len({_digest(r) for r in group})!=1:
            result['reason']='conflicting_provider_records';continue
        try:
            job=_normalize(row,links) if isinstance(row,dict) else None
            if job is None: raise ValueError('invalid_record')
        except ValueError as exc:
            result['reason']=str(exc);continue
        result.update(provider=job['provider'],provider_job_id=job['provider_job_id'],
                      discord_user_id=job['discord_user_id'],miles=str(job['miles']),mileage_type=job['mileage_type'])
        prior=historical.get(job['provider_job_id']) if job['provider']=='trucksbook' else None
        if prior is not None:
            result['reason']='already_in_historical_jobs' if prior['discord_user_id']==job['discord_user_id'] else 'historical_driver_conflict'
            if prior['discord_user_id']==job['discord_user_id']:
                result['decision']='already_recorded'
                result['stored_accepted_miles']=str(prior['accepted_distance'])
            # Historical values win; changed export distance never rewrites them.
        elif not job['link_verified']:
            result['reason']='account_ownership_pending'
        else:
            result['reason']='historical_coverage_and_provider_evidence_required'
        normalized.append((job,result))
    pairs=[]
    for index,(first,a) in enumerate(normalized):
        for second,b in normalized[index+1:]:
            if first['provider']==second['provider'] or first['discord_user_id']!=second['discord_user_id']:
                continue
            try:
                matches=is_candidate_duplicate(first,second)
            except (ValueError,TypeError,KeyError,InvalidOperation):
                # Incomplete metadata is not evidence that deliveries differ.
                if a['decision']=='pending': a['reason']='comparison_metadata_incomplete'
                if b['decision']=='pending': b['reason']='comparison_metadata_incomplete'
                continue
            if matches:
                pairs.append({'jobs':[[first['provider'],first['provider_job_id']],[second['provider'],second['provider_job_id']]],
                              'decision':'potential_duplicate','authoritative_match':False})
                for result in (a,b):
                    if result['decision']=='pending':result['reason']='potential_cross_provider_duplicate'
    drivers=[]
    for driver in snapshot['drivers']:
        user=driver['discord_user_id']
        known=sum(Decimal(str(j['accepted_distance'])) for j in snapshot['processed_jobs']
                  if j['discord_user_id']==user and classify_event_mileage('trucksbook',j['statistics']) is not None)
        current=Decimal(str(driver['real_miles']))
        drivers.append({'discord_user_id':user,'current_real_miles':str(current),
                        'processed_real_miles':str(known),'unexplained_balance':str(current-known),
                        'approved_additions':'0','proposed_real_total':str(current)})
    return {'mode':'read_only_preview','snapshot_sha256':_digest(snapshot),'records_sha256':_digest(records),
            'input_rows':len(records),'unique_records':len(rows),'replays':sum(r['replays'] for r in rows),
            'counts':dict(Counter(r['reason'] for r in rows)), 'drivers':drivers,'records':rows,'candidate_pairs':pairs,
            'mileage_awarded':False,'award_authorized':False}


async def _run(args):
    import asyncpg
    records=json.loads(Path(args.records).read_text())
    pool=await asyncpg.create_pool(os.environ['HISTORICAL_PREVIEW_DATABASE_URL'], min_size=1,max_size=1)
    try:
        snapshot=await read_historical_snapshot(pool)
    finally:
        await pool.close()
    report=build_historical_preview(snapshot,records)
    # A private new file only: never overwrite the input/export or an earlier report.
    with open(args.output,'x',encoding='utf-8',opener=lambda path,flags:os.open(path,flags,0o600)) as output:
        json.dump(report,output,indent=2)
    print(json.dumps({'status':'preview_saved','input_rows':report['input_rows'],'mileage_awarded':False}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records',required=True,help='Normalized candidate JSON; original exports require a validated adapter')
    parser.add_argument('--output',required=True)
    try:
        asyncio.run(_run(parser.parse_args()))
    except Exception:
        # Database errors can contain connection information; never print them.
        raise SystemExit('Preview failed; check input format, read-only database access and output path') from None
