"""Adapters for the owner's observed company export columns; never authenticate claims.

Names are retained as review labels only. No provider account or Discord ID is
invented. Naive export timestamps are preserved but not assigned a timezone.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import csv
import hashlib
import re
from world_of_trucks_policy_v2 import classify_job_mileage


TB_REQUIRED={'Name','Game','From','To','Cargo','Accepted distance','Maximal reached speed','TrucksBookID','Date','Time'}
TRUCKY_REQUIRED={'#','Last Update','Started at','Game','Driver','Status','Source City','Destination City','Cargo',
                 'Distance (km)','Max Speed','Stats Type','Timezone','Company ID'}


def _quantity(value, units):
    if not isinstance(value,str):raise ValueError('Missing explicit quantity unit')
    match=re.fullmatch(r'\s*([0-9]+(?:[ ,][0-9]{3})*(?:\.[0-9]+)?)\s*('+ '|'.join(re.escape(u) for u in units)+r')\s*',value)
    if not match:raise ValueError('Unsupported quantity or unit')
    return Decimal(match[1].replace(' ','').replace(',','')),match[2]


def _number(value):
    if isinstance(value,bool):raise ValueError('Invalid numeric value')
    try:result=Decimal(str(value))
    except InvalidOperation:raise ValueError('Invalid numeric value') from None
    if not result.is_finite() or result<0:raise ValueError('Invalid numeric value')
    return result


def _timestamp(value):
    if isinstance(value,datetime):return value.isoformat()
    return str(value) if value is not None else None


def _base(provider,job_id,game,name,origin,destination,cargo):
    job_id=str(job_id)
    if not job_id.isascii() or not job_id.isdecimal() or int(job_id)<=0:
        raise ValueError('Invalid provider job ID')
    if game not in ('ETS2','ATS'):raise ValueError('Unknown export game')
    return {'provider':provider,'provider_job_id':job_id,'provider_account_id':None,
            'candidate_driver_label':name,'game':game,
            'metadata':{'game':game,'origin':origin,'destination':destination,'cargo':cargo},
            'delivered_at':None,'ownership_verified':False,'eligible_for_award':False,
            'evidence_issues':['stable_account_identity_missing','export_timestamp_semantics_unverified']}


def trucksbook_export_row(row):
    if not TB_REQUIRED.issubset(row):raise ValueError('TrucksBook export columns changed')
    result=_base('trucksbook',row['TrucksBookID'],row['Game'],row['Name'],row['From'],row['To'],row['Cargo'])
    distance,unit=_quantity(row['Accepted distance'],('mi','km'))
    speed,speed_unit=_quantity(row['Maximal reached speed'],('mph','km/h'))
    result.update(distance=str(distance),unit=unit,max_speed=str(speed),speed_unit=speed_unit,
                  statistics=None,source_completion_local=f"{row['Date']}T{row['Time']}")
    # This CSV contains Job Type but no Real/Race statistics column. ETS2 must
    # be checked against provider evidence, not guessed from its market type.
    kind=classify_job_mileage('trucksbook',None,game=row['Game'],max_speed=speed,speed_unit=speed_unit)
    result['company_policy_classification']=kind.mileage_type if kind else None
    if row['Game']=='ETS2':result['evidence_issues'].append('ets2_provider_classification_missing')
    if row['Game']=='ATS' and kind is None:result['evidence_issues'].append('race_excluded_or_speed_pending')
    return result


def trucky_export_row(row):
    if not TRUCKY_REQUIRED.issubset(row):raise ValueError('Trucky export columns changed')
    if row['Company ID'] != 49041:raise ValueError('Unexpected Trucky company')
    result=_base('trucky',row['#'],row['Game'],row['Driver'],row['Source City'],row['Destination City'],row['Cargo'])
    # The column explicitly says km; Distance Unit is a separate display field.
    distance=_number(row['Distance (km)']);speed=_number(row['Max Speed'])
    statistics={'real_miles':'Real','race_miles':'Race'}.get(row['Stats Type'])
    result.update(distance=str(distance),unit='km',max_speed=str(speed),speed_unit=None,
                  statistics=statistics,source_statistics=row['Stats Type'],source_status=row['Status'],
                  source_last_update=_timestamp(row['Last Update']),source_started_at=_timestamp(row['Started at']),
                  source_timezone=row['Timezone'])
    # Timezone metadata does not establish whether Last Update is UTC/local,
    # nor prove that the latest update is the authoritative completion time.
    if row['Status']!='completed':result['evidence_issues'].append('job_not_completed')
    if statistics is None:result['evidence_issues'].append('unsupported_provider_classification')
    if row['Game']=='ATS':result['evidence_issues'].append('max_speed_unit_unverified')
    kind=classify_job_mileage('trucky',statistics,game=row['Game'],max_speed=speed,speed_unit=None) if statistics else None
    result['company_policy_classification']=kind.mileage_type if kind else None
    return result


def read_trucksbook_csv(path):
    with open(path,encoding='utf-8-sig',newline='') as source:
        reader=csv.DictReader(source,delimiter=';')
        if not TB_REQUIRED.issubset(reader.fieldnames or []):raise ValueError('TrucksBook export columns changed')
        return [trucksbook_export_row(row) for row in reader]


def export_manifest(path, records):
    from collections import Counter
    with open(path,'rb') as source:digest=hashlib.file_digest(source,'sha256').hexdigest()
    refs=[(r['provider'],r['provider_job_id']) for r in records]
    return {'source_sha256':digest,'rows':len(records),'unique_provider_jobs':len(set(refs)),
            'games':dict(Counter(r['game'] for r in records)),
            'company_policy_classifications':dict(Counter(r['company_policy_classification'] or 'pending' for r in records)),
            'evidence_issues':dict(Counter(i for r in records for i in r['evidence_issues'])),
            'mileage_awarded':False}
