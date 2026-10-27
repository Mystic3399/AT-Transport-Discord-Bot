"""Read-only race inventory from authenticated provider history, never credits.

Account verification and classification are separate. Export names do not link
accounts, and values here never feed driver totals, ranks or Company Mods.
"""
from collections import defaultdict
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation
import json
from unified_distance_units import KM_TO_MILES
from world_of_trucks_policy_v2 import classify_job_mileage

CONFLICT_KEYS=('game_id','status','stats_type','driven_distance_km','distance_unit',
               'completed_at','max_speed_kmh')

def merge_history_observations(rows):
    """Missing new fields are not conflicts; changed explicit evidence is."""
    accounts={r['provider_account_id'] for r in rows}
    if len(accounts)!=1:return None
    merged={}
    for key in CONFLICT_KEYS:
        values=[]
        for row in rows:
            e=row['evidence']
            if isinstance(e,str):e=json.loads(e)
            if e.get(key) is not None:values.append(e[key])
        if not values:continue
        def comparable(value):
            if key in ('driven_distance_km','max_speed_kmh') and type(value) in (int,float,str):
                try:
                    d=Decimal(str(value))
                    if d.is_finite():return d
                except InvalidOperation:pass
            return (type(value).__name__,str(value))
        if len({comparable(v) for v in values})>1:return None
        merged[key]=values[0]
    account=next(iter(accounts))
    if not isinstance(account,str) or not account.isascii() or not account.isdecimal() or int(account)<=0:return None
    return {'provider_account_id':account,'evidence':merged}

def summarize_pending_trucky_mileage(rows,verified_accounts,*,credited_jobs=(),target_accounts=None,now=None,mileage_type='race'):
    if mileage_type not in ('real','race'):
        raise ValueError('Unsupported mileage type')
    groups=defaultdict(list)
    for row in rows:
        if row.get('provider')=='trucky' and row.get('source_kind')=='authenticated_api':
            groups[row['provider_job_id']].append(row)
    result=dict(verified_pending_jobs=0,verified_pending_miles=Decimal(0),
        unverified_jobs=0,unverified_miles=Decimal(0),classification_pending_jobs=0,
        conflicting_jobs=0,already_credited_jobs=0,mileage_awarded=False,ready_to_award=0)
    now=datetime.now(timezone.utc) if now is None else now
    for job_id,versions in groups.items():
        if target_accounts is not None and not any(r['provider_account_id'] in target_accounts for r in versions):continue
        if job_id in credited_jobs:
            result['already_credited_jobs']+=1;continue
        merged=merge_history_observations(versions)
        if merged is None:
            result['conflicting_jobs']+=1;continue
        e=merged['evidence'];account=merged['provider_account_id']
        stats={'real_miles':'Real','race_miles':'Race'}.get(e.get('stats_type'))
        game={1:'ETS2',2:'ATS'}.get(e.get('game_id')) if type(e.get('game_id')) is int else None
        if e.get('status')!='completed' or stats is None:continue
        kind=classify_job_mileage('trucky',stats,game=game,max_speed=e.get('max_speed_kmh'),speed_unit='km/h')
        if kind is None:
            result['classification_pending_jobs']+=1;continue
        if kind.mileage_type!=mileage_type:continue
        try:
            km=Decimal(str(e.get('driven_distance_km')))
            completed=datetime.fromisoformat(e['completed_at'].replace('Z','+00:00'))
            valid=(type(e.get('driven_distance_km')) is not bool and km.is_finite() and 0<km<=100000
                and e.get('distance_unit')=='km' and completed.tzinfo is not None
                and completed.utcoffset() is not None and completed<=now)
        except (ValueError,TypeError,KeyError,AttributeError,InvalidOperation):valid=False
        if not valid:
            result['classification_pending_jobs']+=1;continue
        bucket='verified_pending' if account in verified_accounts else 'unverified'
        result[bucket+'_jobs']+=1;result[bucket+'_miles']+=km*KM_TO_MILES
    return result

def summarize_pending_race(rows,verified_accounts,*,credited_jobs=(),target_accounts=None,now=None):
    return summarize_pending_trucky_mileage(rows,verified_accounts,credited_jobs=credited_jobs,
        target_accounts=target_accounts,now=now,mileage_type='race')


def build_pending_account_inventory(rows,links,*,credited_jobs=(),target_accounts=None,now=None):
    """Sort evidence by account; export labels never select a Discord owner.

    Real amounts may overlap legacy TrucksBook credit. Neither amount is an
    approved addition, and a stored verified link is not per-job verification.
    """
    accounts=defaultdict(set)
    labels=defaultdict(set)
    for row in rows:
        if row.get('provider')!='trucky':continue
        if row.get('source_kind')=='authenticated_api':
            account=row.get('provider_account_id')
            if isinstance(account,str) and account.isascii() and account.isdecimal() and int(account)>0:
                accounts[account].add(row['provider_job_id'])
        elif row.get('source_kind')=='owner_export':
            evidence=row.get('evidence') or {}
            if isinstance(evidence,str):evidence=json.loads(evidence)
            label=evidence.get('candidate_driver_label')
            if isinstance(label,str) and label.strip():
                labels[row['provider_job_id']].add(label.strip()[:100])
    result=[]
    for account,jobs in sorted(accounts.items()):
        if target_accounts is not None and account not in target_accounts:continue
        saved=[link for link in links if link.get('provider')=='trucky' and link.get('provider_account_id')==account]
        owners={link.get('discord_user_id') for link in saved}
        real=summarize_pending_trucky_mileage(rows,set(),credited_jobs=credited_jobs,
            target_accounts={account},now=now,mileage_type='real')
        race=summarize_pending_race(rows,set(),credited_jobs=credited_jobs,target_accounts={account},now=now)
        result.append(dict(provider='trucky',provider_account_id=account,
            candidate_labels=sorted({label for job in jobs for label in labels[job]})[:10],
            linked_discord_user_id=next(iter(owners)) if len(owners)==1 else None,
            link_status='conflicting_saved_links' if len(owners)>1 else
                'linked_job_verification_pending' if saved else 'account_link_required',
            authenticated_jobs=len(jobs),pending_real_jobs=real['unverified_jobs'],
            pending_real_miles=str(real['unverified_miles']),pending_race_jobs=race['unverified_jobs'],
            pending_race_miles=str(race['unverified_miles']),
            excluded_or_pending_jobs=max(0,len(jobs)-real['unverified_jobs']-race['unverified_jobs']-race['already_credited_jobs']),
            conflicting_jobs=race['conflicting_jobs'],approved_addition='0'))
    return dict(accounts=result,accounts_without_link=sum(a['link_status']=='account_link_required' for a in result),
        labels_are_not_ownership_evidence=True,real_miles_require_cross_provider_reconciliation=True,
        mileage_awarded=False,registration_changed=False)


async def read_pending_account_inventory(pool,discord_user_id=None):
    if discord_user_id is not None and (type(discord_user_id) is not int or discord_user_id<=0):
        raise ValueError('Invalid driver')
    async with pool.acquire() as c:
        async with c.transaction(isolation='repeatable_read',readonly=True):
            if not await c.fetchval("SELECT to_regclass('public.pending_provider_job_history_v2') IS NOT NULL"):
                return build_pending_account_inventory([],[])
            rows=[dict(r) for r in await c.fetch("SELECT provider,provider_job_id,provider_account_id,source_kind,evidence FROM pending_provider_job_history_v2 WHERE provider='trucky'")]
            links=[dict(r) for r in await c.fetch("SELECT provider,provider_account_id,discord_user_id FROM unified_provider_links WHERE provider='trucky'")]
            target=None if discord_user_id is None else {r['provider_account_id'] for r in links if r['discord_user_id']==discord_user_id}
            credited=set()
            if await c.fetchval("SELECT to_regclass('public.unified_v2_production_credit_ledger') IS NOT NULL"):
                credited={r['provider_job_id'] for r in await c.fetch("SELECT provider_job_id FROM unified_v2_production_credit_ledger WHERE provider='trucky'")}
    return build_pending_account_inventory(rows,links,credited_jobs=credited,target_accounts=target)


async def read_pending_race(pool,discord_user_id=None):
    if discord_user_id is not None and (type(discord_user_id) is not int or discord_user_id<=0):
        raise ValueError('Invalid driver')
    async with pool.acquire() as c:
        async with c.transaction(isolation='repeatable_read',readonly=True):
            if not await c.fetchval("SELECT to_regclass('public.pending_provider_job_history_v2') IS NOT NULL"):
                return summarize_pending_race([],set())
            rows=[dict(r) for r in await c.fetch("SELECT provider,provider_job_id,provider_account_id,source_kind,evidence FROM pending_provider_job_history_v2 WHERE provider='trucky' AND source_kind='authenticated_api'")]
            owners=await c.fetch('''SELECT l.provider_account_id FROM unified_provider_links l
                JOIN unified_steam_links s ON s.discord_user_id=l.discord_user_id AND s.verified_at IS NOT NULL
                JOIN unified_driver_profiles p ON p.discord_user_id=l.discord_user_id AND p.status='active'
                JOIN unified_provider_claims c ON c.provider=l.provider AND c.provider_account_id=l.provider_account_id
                    AND c.discord_user_id=l.discord_user_id AND c.status='approved'
                    AND c.ownership_confirmed=TRUE AND c.membership_confirmed=TRUE
                    AND c.ownership_evidence_sha256 IS NOT NULL
                    AND c.ownership_evidence_kind='trucky_signed_job_steam_match'
                    AND (c.expires_at IS NULL OR c.expires_at>NOW())
                WHERE l.provider='trucky' AND l.verified_at IS NOT NULL''')
            target=None
            if discord_user_id is not None:
                target={r['provider_account_id'] for r in await c.fetch("SELECT provider_account_id FROM unified_provider_links WHERE provider='trucky' AND discord_user_id=$1",discord_user_id)}
            credited=set()
            if await c.fetchval("SELECT to_regclass('public.unified_v2_production_credit_ledger') IS NOT NULL"):
                credited={r['provider_job_id'] for r in await c.fetch("SELECT provider_job_id FROM unified_v2_production_credit_ledger WHERE provider='trucky'")}
    return summarize_pending_race(rows,{r['provider_account_id'] for r in owners},credited_jobs=credited,target_accounts=target)
