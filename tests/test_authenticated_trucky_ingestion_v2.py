"""The live API bridge persists verified evidence; it never awards mileage."""
import unittest
from unittest.mock import AsyncMock, patch
import trucky_jobs_api_v2 as api
from tests.test_trucky_verified_identity_lookup_v2 import JOB

class Acquire:
    def __init__(self,conn):self.conn=conn
    async def __aenter__(self):return self.conn
    async def __aexit__(self,*args):pass

class Pool:
    def __init__(self,owners=None,receipt=True,previous_payload=None):
        self.fetch=AsyncMock(return_value=owners if owners is not None else [dict(discord_user_id=789,provider_account_id='456')])
        self.fetchval=AsyncMock(side_effect=lambda sql,*args: previous_payload if 'SELECT source_payload' in sql else receipt)
        self.execute=AsyncMock(return_value="UPDATE 1")
    def acquire(self):return Acquire(self)

class AuthenticatedTruckyIngestionTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_sweep_disabled_before_database_access(self):
        import os
        for env in ({'AT_V2_PENDING_HISTORY_ENABLED':'NO'},
                    {'AT_V2_PENDING_HISTORY_ENABLED':'YES','AT_V2_ISOLATED_STAGING':'YES'}):
            with patch.dict(os.environ,env,clear=True):
                self.assertEqual((await api.sweep_saved_trucky_history(None))['status'],'disabled')

    async def test_nonpositive_api_identity_does_not_break_capture_batch(self):
        result,record=await self.run_ingest(Pool(),{**JOB,'user_id':0})
        self.assertEqual(result['counts']['invalid_record'],1)
        record.assert_not_awaited()

    async def run_ingest(self,pool,job=None,inserted=True):
        with patch.object(api,'fetch_trucky_jobs_page',AsyncMock(return_value={'data':[dict(JOB) if job is None else job]})),patch('unified_driver_profiles.record_normalized_provider_job',AsyncMock(return_value=inserted)) as record:
            result=await api.ingest_authenticated_completed_jobs(pool)
        self.assertIs(result['mileage_awarded'],False)
        return result,record

    async def test_verified_completed_record_reaches_existing_normalizer(self):
        pool=Pool();result,record=await self.run_ingest(pool)
        self.assertEqual(result['counts']['recorded'],1)
        self.assertEqual(record.call_args.args[1:4],('trucky','123',789))
        self.assertEqual(record.call_args.kwargs['provider_account_id'],'456')
        sql=pool.fetch.call_args.args[0]
        for condition in ("p.status='active'","c.status='approved'","s.verified_at IS NOT NULL","c.expires_at>NOW()"):
            self.assertIn(condition,sql)

    async def test_missing_or_ambiguous_owner_cannot_record(self):
        for owners in ([],[{},{}]):
            result,record=await self.run_ingest(Pool(owners))
            self.assertEqual(result['counts']['ownership_pending'],1);record.assert_not_awaited()

    async def test_signed_receipt_required_for_individual_job(self):
        result,record=await self.run_ingest(Pool(receipt=False))
        self.assertEqual(result['counts']['signed_receipt_pending'],1);record.assert_not_awaited()

    async def test_repeated_event_is_reported_as_replay_without_credit(self):
        result,record=await self.run_ingest(Pool(),inserted=False)
        self.assertEqual(result['counts']['replayed'],1)

    async def test_unverified_race_schema_or_incomplete_job_stays_pending(self):
        for changes in ({'stats_type':'road_trip_race'},{'status':'in_progress'},{'user_id':999},
                        {'game_id':2,'max_speed_kmh':None}):
            result,record=await self.run_ingest(Pool(),{**JOB,**changes})
            record.assert_not_awaited()
            self.assertEqual(result['counts']['classification_or_completion_pending'],1)

    async def test_ats_race_reaches_separate_normalized_bucket_without_award(self):
        result,record=await self.run_ingest(Pool(),{**JOB,'game_id':2,'max_speed_kmh':132})
        self.assertEqual(result['counts']['recorded'],1)
        self.assertEqual(record.call_args.kwargs['mileage_type'],'race')
        self.assertEqual(record.call_args.kwargs['classification_evidence']['max_speed_kmh'],'132')
        self.assertFalse(result['mileage_awarded'])

    async def test_conflicting_replay_is_quarantined(self):
        with patch.object(api,'fetch_trucky_jobs_page',AsyncMock(return_value={'data':[JOB]})),patch('unified_driver_profiles.record_normalized_provider_job',AsyncMock(side_effect=ValueError('Conflicting replay'))):
            result=await api.ingest_authenticated_completed_jobs(Pool())
        self.assertEqual(result['counts']['conflicting_event_pending'],1)

    async def test_unregistered_completed_job_is_retained_only_with_history_flag(self):
        import os
        with patch.dict(os.environ,{'AT_V2_PENDING_HISTORY_ENABLED':'YES'}),patch('pending_driver_history_v2.record_pending_history',AsyncMock()) as retain:
            result,record=await self.run_ingest(Pool(owners=[]))
        retain.assert_awaited_once()
        self.assertEqual(retain.call_args.args[1:4],('trucky','123','456'))
        self.assertEqual(retain.call_args.kwargs,{'source_kind':'authenticated_api'})
        self.assertEqual(result['counts']['ownership_pending'],1)
        record.assert_not_awaited()

    async def test_authenticated_route_and_cargo_reach_reconciliation_storage(self):
        route=dict(source_city_name='Praha',destination_city_name='Lille',cargo_name='Pet Food')
        result,record=await self.run_ingest(Pool(),{**JOB,**route})
        self.assertEqual(record.call_args.kwargs['job_metadata'],
            dict(game='ETS2',origin='Praha',destination='Lille',cargo='Pet Food'))
        self.assertEqual(result['counts']['recorded'],1)
        self.assertFalse(result['mileage_awarded'])

    async def test_incomplete_route_is_not_invented(self):
        for route in ({'source_city_name':'Praha'},
                      {'source_city_name':'Praha','destination_city_name':'Lille','cargo_name':{'name':'Pet Food'}},
                      {'source_city_name':'x'*301,'destination_city_name':'Lille','cargo_name':'Pet Food'}):
            _,record=await self.run_ingest(Pool(),{**JOB,**route})
            self.assertNotIn('job_metadata',record.call_args.kwargs)

    async def test_history_retains_provider_metadata_without_linking_driver_or_awarding(self):
        import os
        route=dict(source_city_name='Paris',destination_city_name='Lille',cargo_name='Pet Food')
        with patch.dict(os.environ,{'AT_V2_PENDING_HISTORY_ENABLED':'YES'}),patch('pending_driver_history_v2.record_pending_history',AsyncMock()) as retain:
            result,record=await self.run_ingest(Pool(owners=[]),{**JOB,**route})
        evidence=retain.call_args.args[4]
        self.assertEqual({k:evidence[k] for k in ('game','origin','destination','cargo')},
            dict(game='ETS2',origin='Paris',destination='Lille',cargo='Pet Food'))
        self.assertNotIn('discord_user_id',evidence)
        self.assertNotIn('shared_journey_id',evidence)
        record.assert_not_awaited()
        self.assertFalse(result['mileage_awarded'])

    async def test_old_metadata_less_event_replays_without_rewriting_its_audit(self):
        route=dict(source_city_name='Paris',destination_city_name='Lille',cargo_name='Pet Food')
        result,record=await self.run_ingest(Pool(previous_payload={'job_metadata':None}),{**JOB,**route},inserted=False)
        self.assertNotIn('job_metadata',record.call_args.kwargs)
        self.assertEqual(result['counts']['replayed'],1)
        self.assertFalse(result['mileage_awarded'])

    async def test_existing_metadata_is_still_compared_and_invalid_stored_payload_is_held(self):
        route=dict(source_city_name='Paris',destination_city_name='Lille',cargo_name='Pet Food')
        _,record=await self.run_ingest(Pool(previous_payload={'job_metadata':route}),{**JOB,**route})
        self.assertIn('job_metadata',record.call_args.kwargs)
        result,record=await self.run_ingest(Pool(previous_payload=True),{**JOB,**route})
        self.assertEqual(result['counts']['conflicting_event_pending'],1)
        record.assert_not_awaited()
