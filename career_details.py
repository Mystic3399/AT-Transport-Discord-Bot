"""Read-only driver career projection from already credited delivery records."""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
import json

UK = ZoneInfo('Europe/London')
TARGET = 10000
CREDITED_SQL = """
WITH jobs AS (
 SELECT 'trucksbook'::text provider, p.job_id, p.discord_user_id,
        p.accepted_distance::numeric miles, 'real'::text mileage_type,
        p.processed_at delivered_at
 FROM processed_jobs p
 WHERE p.discord_user_id=ANY($1::bigint[]) AND p.accepted_distance>0
   AND LOWER(p.statistics) LIKE 'real%'
   AND NOT EXISTS(SELECT 1 FROM unified_v2_production_mileage_decisions d
       WHERE d.provider='trucksbook' AND d.provider_job_id=p.job_id
         AND d.discord_user_id=p.discord_user_id AND d.decision='duplicate_provider_job'
         AND d.reason_code='superseded_by_higher_real_credit')
 UNION ALL
 SELECT c.provider, c.provider_job_id, c.discord_user_id, c.miles,
        c.mileage_type, c.delivered_at
 FROM unified_v2_production_credit_ledger c
 WHERE c.provider='trucky' AND c.discord_user_id=ANY($1::bigint[])
)
SELECT j.*, e.source_payload FROM jobs j
LEFT JOIN LATERAL (SELECT source_payload FROM unified_provider_job_events e
 WHERE e.provider=j.provider AND e.provider_job_id=j.job_id
 AND e.discord_user_id=j.discord_user_id LIMIT 1) e ON TRUE
ORDER BY j.delivered_at DESC NULLS LAST, j.provider, j.job_id
"""

def _label(value):
    if isinstance(value, dict):
        return _label(value.get('name') or value.get('city') or value.get('city_name'))
    return ' '.join(str(value or '').split())[:100]

def delivery(row):
    payload = row.get('source_payload') or {}
    if isinstance(payload, str):
        try: payload = json.loads(payload)
        except (ValueError, TypeError): payload = {}
    if not isinstance(payload, dict): payload = {}
    metadata = payload.get('job_metadata') or payload.get('classification_evidence') or {}
    if not isinstance(metadata, dict): metadata = {}
    evidence = payload.get('evidence') or {}
    if not isinstance(evidence, dict): evidence = {}
    sources = (metadata, evidence, payload)
    def pick(*keys):
        return next((_label(s[k]) for s in sources for k in keys if s.get(k)), '')
    game = pick('game').upper()
    game = {'EUROTRUCKSIMULATOR2':'ETS2','AMERICANTRUCKSIMULATOR':'ATS','EURO TRUCK SIMULATOR 2':'ETS2','AMERICAN TRUCK SIMULATOR':'ATS'}.get(game,game)
    stamp = row.get('delivered_at')
    return dict(id=str(row['job_id']), provider='Trucky' if row['provider']=='trucky' else 'TrucksBook',
        type=row['mileage_type'], miles=float(row['miles']),
        game=game if game in ('ATS','ETS2') else None,
        origin=pick('source_city_name','origin','source_city','departure_city'),
        destination=pick('destination_city_name','destination','destination_city'),
        cargo=pick('cargo_name','cargo'), deliveredAt=stamp.isoformat() if stamp else None)

def summarise(rows, now=None):
    now=(now or datetime.now(UK)).astimezone(UK)
    monthly=defaultdict(Decimal); counts=defaultdict(int)
    for row in rows:
        if row['mileage_type']!='real' or not row.get('delivered_at'): continue
        stamp=row['delivered_at'].astimezone(UK)
        key=f'{stamp.year:04d}-{stamp.month:02d}'
        monthly[key]+=Decimal(str(row['miles'])); counts[key]+=1
    key=now.strftime('%Y-%m'); monthly_miles=int(monthly.get(key,0))
    year,month=now.year,now.month
    if monthly_miles<TARGET:
        month-=1
        if not month: year-=1; month=12
    streak=0
    while int(monthly.get(f'{year:04d}-{month:02d}',0))>=TARGET:
        streak+=1; month-=1
        if not month: year-=1; month=12
    best=max(monthly, key=lambda k: monthly[k],default=None)
    return dict(monthlyRealMiles=monthly_miles, monthlyTarget=TARGET,
        monthlyLabel=now.strftime('%B %Y'), targetPercent=round(monthly_miles/TARGET*100,1),
        targetStreak=streak, bestMonthMiles=int(monthly[best]) if best else None,
        bestMonth=best, mostMonthlyJobs=max(counts.values(),default=None),
        historyJobs=len(rows), deliveries=[delivery(dict(r)) for r in rows[:100]],
        monthlyHistory={k:int(v) for k,v in monthly.items()})

async def fetch(connection, user_ids):
    if not user_ids: return {}
    rows=await connection.fetch(CREDITED_SQL,list(user_ids))
    grouped=defaultdict(list)
    for r in rows: grouped[r['discord_user_id']].append(dict(r))
    return {uid:summarise(grouped[uid]) for uid in user_ids}

def division_choices(member):
    names=' '.join(r.name.casefold() for r in member.roles)
    choices=[]
    if 'recovery' in names: choices.append('Recovery')
    if 'heavy haul' in names: choices.append('Heavy Haulage')
    choices.append('Transport')
    return choices
