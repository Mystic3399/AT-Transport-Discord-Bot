import unittest
from unittest.mock import AsyncMock
from tests.test_authenticated_trucky_ingestion_v2 import Acquire
from pending_driver_history_v2 import record_pending_history,linked_history_summary
from historical_provider_exports_v2 import trucksbook_speed_check
from tests.test_historical_provider_exports_v2 import TB

class PendingHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_evidence_written_and_account_not_discord_identity(self):
        conn=type('Conn',(),{})();conn.execute=AsyncMock()
        conn.transaction=lambda **kwargs:Acquire(None)
        pool=type('Pool',(),{'acquire':lambda self:Acquire(conn)})()
        result=await record_pending_history(pool,'trucky','123','456',{'status':'completed','driven_distance_km':586},source_kind='authenticated_api')
        self.assertFalse(result['mileage_awarded'])
        sql=conn.execute.call_args.args[0]
        self.assertIn('INSERT INTO pending_provider_job_history_v2',sql)
        self.assertNotIn('driver_progress',sql)
        self.assertNotIn('unified_provider_links',sql)
        self.assertIn('replay_count',sql)
        self.assertEqual(conn.execute.call_args.args[5],'authenticated_api')

    async def test_raw_secrets_and_missing_api_account_rejected(self):
        for account,evidence in [(None,{'status':'completed'}),('456',{'token':'secret'}),('456',{'status':{'nested':'payload'}})]:
            with self.assertRaises(ValueError):
                await record_pending_history(None,'trucky','123',account,evidence,source_kind='authenticated_api')

    async def test_export_without_account_retained_but_never_assumed_owned(self):
        conn=type('Conn',(),{})();conn.execute=AsyncMock();conn.transaction=lambda **kwargs:Acquire(None)
        pool=type('Pool',(),{'acquire':lambda self:Acquire(conn)})()
        await record_pending_history(pool,'trucksbook','123',None,{'candidate_driver_label':'redacted','distance':'365','unit':'mi'},source_kind='owner_export')
        self.assertIsNone(conn.execute.call_args.args[4])

class TrucksBookSpeedCheckTests(unittest.TestCase):
    def test_ats_and_ets2_separate_limits_no_new_awards(self):
        report=trucksbook_speed_check([TB,{**TB,'Game':'ETS2'},{**TB,'Game':'ETS2','Maximal reached speed':'62 mph'},
                                     {**TB,'Maximal reached speed':'82 mph'}])
        driver=report['drivers'][0]
        self.assertEqual(driver['ats_real'],'1234')
        self.assertEqual(driver['ets2_real'],'1234')
        self.assertEqual(driver['race_excluded'],'2468')
        self.assertEqual(driver['approved_addition'],'0')
        self.assertFalse(report['safe_to_add_to_existing_totals'])

class ImportPreparationTests(unittest.TestCase):
    def test_existing_jobs_protected_and_missing_jobs_not_assumed_new_credit(self):
        from historical_provider_exports_v2 import prepare_import_review
        from tests.test_historical_provider_exports_v2 import TR
        batch=prepare_import_review([TB,{**TB,'TrucksBookID':'124'}],[TR],{'123'})
        self.assertEqual(batch['records'][0]['import_status'],'already_recorded')
        self.assertEqual(batch['records'][1]['import_status'],'pending_evidence')
        self.assertEqual(batch['ready_to_award'],0)
        self.assertFalse(batch['production_write_authorized'])
        self.assertTrue(all(r['approved_addition']=='0' for r in batch['records']))

    def test_duplicate_references_require_explicit_version_review(self):
        from historical_provider_exports_v2 import prepare_import_review
        with self.assertRaises(ValueError):prepare_import_review([TB,TB],[],set())

    def test_similarity_never_confirms_identity_or_shared_journey(self):
        from historical_provider_exports_v2 import prepare_import_review
        from tests.test_historical_provider_exports_v2 import TR
        batch=prepare_import_review([{**TB,'Accepted distance':'364 mi'}],[{**TR,'Driver':TB['Name']}],set())
        self.assertEqual(len(batch['review_leads']),1)
        self.assertFalse(batch['review_leads'][0]['identity_verified'])
        self.assertIsNone(batch['review_leads'][0]['shared_journey_id'])
        self.assertEqual(batch['ready_to_award'],0)
