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
    def __init__(self,owners=None,receipt=True):
        self.fetch=AsyncMock(return_value=owners if owners is not None else [dict(discord_user_id=789,provider_account_id='456')])
        self.fetchval=AsyncMock(return_value=receipt)
    def acquire(self):return Acquire(self)

class AuthenticatedTruckyIngestionTests(unittest.IsolatedAsyncioTestCase):
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
        for changes in ({'stats_type':'race_miles'},{'status':'in_progress'},{'user_id':999}):
            result,record=await self.run_ingest(Pool(),{**JOB,**changes})
            record.assert_not_awaited()
            self.assertEqual(result['counts']['classification_or_completion_pending'],1)

    async def test_conflicting_replay_is_quarantined(self):
        with patch.object(api,'fetch_trucky_jobs_page',AsyncMock(return_value={'data':[JOB]})),patch('unified_driver_profiles.record_normalized_provider_job',AsyncMock(side_effect=ValueError('Conflicting replay'))):
            result=await api.ingest_authenticated_completed_jobs(Pool())
        self.assertEqual(result['counts']['conflicting_event_pending'],1)
