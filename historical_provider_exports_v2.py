"""Adapters for the owner's observed company export columns; never authenticate claims.

Names are retained as review labels only. No provider account or Discord ID is
invented. Naive export timestamps are preserved but not assigned a timezone.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import csv
from collections import Counter
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


def trucky_export_row(row, *, max_speed_unit=None):
    if not TRUCKY_REQUIRED.issubset(row):raise ValueError('Trucky export columns changed')
    if row['Company ID'] != 49041:raise ValueError('Unexpected Trucky company')
    result=_base('trucky',row['#'],row['Game'],row['Driver'],row['Source City'],row['Destination City'],row['Cargo'])
    # The column explicitly says km; Distance Unit is a separate display field.
    distance=_number(row['Distance (km)']);speed=_number(row['Max Speed'])
    # Unit is configured from the owner-confirmed export contract, never
    # inferred from the distance display preference or raw speed magnitude.
    if max_speed_unit not in (None,'km/h','mph'):
        raise ValueError('Unsupported maximum speed unit')
    statistics={'real_miles':'Real','race_miles':'Race'}.get(row['Stats Type'])
    result.update(distance=str(distance),unit='km',max_speed=str(speed),speed_unit=max_speed_unit,
                  max_speed_mph=(str(speed/Decimal('1.609344')) if max_speed_unit=='km/h'
                                 else str(speed) if max_speed_unit=='mph' else None),
                  statistics=statistics,source_statistics=row['Stats Type'],source_status=row['Status'],
                  source_last_update=_timestamp(row['Last Update']),source_started_at=_timestamp(row['Started at']),
                  source_timezone=row['Timezone'])
    # Timezone metadata does not establish whether Last Update is UTC/local,
    # nor prove that the latest update is the authoritative completion time.
    if row['Status']!='completed':result['evidence_issues'].append('job_not_completed')
    if statistics is None:result['evidence_issues'].append('unsupported_provider_classification')
    if row['Game']=='ATS' and max_speed_unit is None:
        result['evidence_issues'].append('max_speed_unit_unverified')
    kind=classify_job_mileage('trucky',statistics,game=row['Game'],max_speed=speed,speed_unit=max_speed_unit) if statistics else None
    result['company_policy_classification']=kind.mileage_type if kind else None
    if row['Game']=='ATS' and statistics and max_speed_unit is not None and kind is None:
        result['evidence_issues'].append('ats_speed_outside_approved_ranges')
    if kind and statistics and kind.mileage_type!=statistics.lower():
        result['evidence_issues'].append('company_policy_differs_from_provider_classification')
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


def trucksbook_speed_check(rows):
    """Truck-mode export arithmetic only; never authorize an import delta.

    Existing ETS2 limit: 100 km/h / displayed 62 mph. ATS uses the owner's
    0–81 mph Real, 82–112 mph Race policy. Values in display gaps stay pending.
    Names group this report only; registration ownership is checked separately.
    """
    from collections import defaultdict
    totals=defaultdict(lambda:{'jobs':0,'ets2_real':Decimal(0),'ats_real':Decimal(0),
                              'race_excluded':Decimal(0),'pending':Decimal(0)})
    for row in rows:
        normalized=trucksbook_export_row(row)
        distance,unit=_quantity(row['Accepted distance'],('mi','km'))
        from unified_distance_units import KM_TO_MILES
        miles=distance*(KM_TO_MILES if unit=='km' else Decimal(1))
        speed,speed_unit=_quantity(row['Maximal reached speed'],('mph','km/h'))
        driver=totals[row['Name']];driver['jobs']+=1
        if row['Game']=='ATS':
            mph=speed if speed_unit=='mph' else speed/Decimal('1.609344')
            bucket='ats_real' if mph<=81 else 'race_excluded' if 82<=mph<=112 else 'pending'
        else:
            limit=Decimal(62) if speed_unit=='mph' else Decimal(100)
            race_min=Decimal(63) if speed_unit=='mph' else Decimal(101)
            race_max=Decimal(112) if speed_unit=='mph' else Decimal(180)
            bucket='ets2_real' if speed<=limit else 'race_excluded' if race_min<=speed<=race_max else 'pending'
        driver[bucket]+=miles
    output=[]
    for name,values in sorted(totals.items(),key=lambda item:item[0].casefold()):
        output.append({'candidate_driver_label':name,'jobs':values['jobs'],
                       **{key:str(value) for key,value in values.items() if key!='jobs'},
                       'combined_real':str(values['ets2_real']+values['ats_real']),
                       'approved_addition':'0'})
    return {'basis':'export_speed_check_not_provider_authentication','drivers':output,
            'combined_real':str(sum((Decimal(r['combined_real']) for r in output),Decimal(0))),
            'ats_real':str(sum((Decimal(r['ats_real']) for r in output),Decimal(0))),
            'race_excluded':str(sum((Decimal(r['race_excluded']) for r in output),Decimal(0))),
            'pending':str(sum((Decimal(r['pending']) for r in output),Decimal(0))),
            'mileage_awarded':False,'safe_to_add_to_existing_totals':False}


def prepare_import_review(trucksbook_rows, trucky_rows, processed_trucksbook_ids):
    """Build an evidence-only batch; this is not a credit or profile import.

    Existing provider-scoped IDs are protected even if export labels changed.
    Missing IDs are not proof of missing credit. Metadata/name similarity only
    generates review leads; no canonical journey ID or account is invented.
    """
    from unified_job_matching_v2 import _normalized_metadata
    from unified_distance_units import KM_TO_MILES
    records=[];seen=set()
    for original in trucksbook_rows:
        record=trucksbook_export_row(original)
        check=trucksbook_speed_check([original])
        driver=check['drivers'][0]
        policy='real' if Decimal(driver['combined_real'])>0 else 'race_excluded' if Decimal(driver['race_excluded'])>0 else 'pending'
        record['speed_check_classification']=policy
        record['import_status']=('already_recorded' if record['provider_job_id'] in processed_trucksbook_ids else
                                 'excluded_by_policy' if policy=='race_excluded' else 'pending_evidence')
        records.append(record)
    for original in trucky_rows:
        record=trucky_export_row(original,max_speed_unit='km/h')
        record['import_status']='excluded_by_policy' if record['source_statistics']=='invalid' else 'pending_evidence'
        records.append(record)
    for record in records:
        reference=(record['provider'],record['provider_job_id'])
        if reference in seen:raise ValueError('Duplicate provider reference: reconcile export versions first')
        seen.add(reference)
        record['approved_addition']='0'
    leads=[]
    for first in records:
        if first['provider']!='trucksbook':continue
        for second in records:
            if second['provider']!='trucky' or first['game']!=second['game']:continue
            # A display label narrows human review only; it never proves identity.
            if ' '.join(str(first['candidate_driver_label']).casefold().split())!=' '.join(str(second['candidate_driver_label']).casefold().split()):continue
            try:
                if _normalized_metadata(first['metadata'])!=_normalized_metadata(second['metadata']):continue
                a=Decimal(first['distance'])*(KM_TO_MILES if first['unit']=='km' else Decimal(1))
                b=Decimal(second['distance'])*(KM_TO_MILES if second['unit']=='km' else Decimal(1))
                if abs(a-b)>5:continue
            except (ValueError,TypeError,InvalidOperation):continue
            leads.append({'trucksbook_job_id':first['provider_job_id'],'trucky_job_id':second['provider_job_id'],
                          'status':'potential_duplicate','identity_verified':False,'timestamps_verified':False,
                          'shared_journey_id':None,'mileage_awarded':False})
    return {'mode':'review_only','records':records,'review_leads':leads,
            'counts':dict(Counter(r['import_status'] for r in records)),
            'records_count':len(records),'ready_to_award':0,'mileage_awarded':False,
            'production_write_authorized':False}


def provider_coverage_review(records, candidate_pairs):
    """Separate unmatched jobs from possible overlaps; never discard singles.

    Review leads do not establish driver identity, feed completeness or a
    canonical duplicate. Single-provider jobs remain valid import candidates,
    subject to ownership, historical coverage and normal award gates.
    """
    from collections import defaultdict
    from unified_distance_units import KM_TO_MILES
    refs={(r['provider'],r['provider_job_id']):r for r in records}
    if len(refs)!=len(records):raise ValueError('Conflicting or repeated provider reference')
    neighbours=defaultdict(set)
    for pair in candidate_pairs:
        first=('trucksbook',pair['trucksbook_job_id']);second=('trucky',pair['trucky_job_id'])
        if first not in refs or second not in refs:raise ValueError('Review references an absent job')
        neighbours[first].add(second);neighbours[second].add(first)
    rows=[];counts=defaultdict(Counter)
    for ref,record in refs.items():
        adjacent=neighbours[ref]
        ambiguous=len(adjacent)>1 or any(len(neighbours[other])>1 for other in adjacent)
        category='ambiguous_overlap' if ambiguous else 'possible_overlap' if adjacent else 'single_provider_candidate'
        kind=record.get('speed_check_classification') if ref[0]=='trucksbook' else record.get('company_policy_classification')
        amount=Decimal(record['distance'])*(KM_TO_MILES if record['unit']=='km' else Decimal(1))
        rows.append({'provider':ref[0],'provider_job_id':ref[1],'driver_label':record['candidate_driver_label'],
                     'category':category,'counterpart_candidates':len(adjacent),
                     'policy_classification':kind or 'pending','miles':str(amount),
                     'existing_import_status':record['import_status'],'approved_addition':'0'})
        counts[ref[0]][category]+=1
    return {'records':rows,'counts':{provider:dict(count) for provider,count in counts.items()},
            'counterpart_required_for_eligibility':False,'absence_proves_new_credit':False,
            'confirmed_duplicates':0,'mileage_awarded':False}
