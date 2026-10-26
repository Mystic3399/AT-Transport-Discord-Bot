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

class ProviderCoverageReviewTests(unittest.TestCase):
    def records(self):
        from historical_provider_exports_v2 import prepare_import_review
        from tests.test_historical_provider_exports_v2 import TR
        return prepare_import_review([TB],[TR],set())['records']

    def test_independent_provider_jobs_retained_instead_of_rejected_for_missing_match(self):
        from historical_provider_exports_v2 import provider_coverage_review
        report=provider_coverage_review(self.records(),[])
        self.assertEqual(report['counts'],{'trucksbook':{'single_provider_candidate':1},'trucky':{'single_provider_candidate':1}})
        self.assertFalse(report['counterpart_required_for_eligibility'])
        self.assertFalse(report['absence_proves_new_credit'])
        self.assertTrue(all(r['approved_addition']=='0' for r in report['records']))

    def test_shared_leads_are_review_only_and_many_to_one_is_ambiguous(self):
        from historical_provider_exports_v2 import provider_coverage_review
        records=self.records();records.append({**records[0],'provider_job_id':'124'})
        pairs=[{'trucksbook_job_id':id,'trucky_job_id':'456'} for id in ('123','124')]
        report=provider_coverage_review(records,pairs)
        self.assertTrue(all(r['category']=='ambiguous_overlap' for r in report['records']))
        self.assertEqual(report['confirmed_duplicates'],0)
        with self.assertRaises(ValueError):provider_coverage_review(records,[{'trucksbook_job_id':'999','trucky_job_id':'456'}])

class PendingTrucksBookSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_verified_source_retained_without_driver_or_unit_inference(self):
        from pending_driver_history_v2 import retain_trucksbook_source_observation
        from unittest.mock import patch
        with patch('pending_driver_history_v2.record_pending_history',AsyncMock(return_value={'mileage_awarded':False})) as store:
            await retain_trucksbook_source_observation(None,webhook_id=123,guild_id=9,expected_guild_id=9,
                configured_webhook_id='123',parsed_job={'job_id':'456','statistics':'Real','accepted_distance':365},driver_label='redacted')
        self.assertIsNone(store.call_args.args[3])
        self.assertIsNone(store.call_args.args[4]['unit'])
        self.assertEqual(store.call_args.kwargs['source_kind'],'verified_webhook_source')

    async def test_wrong_source_or_guild_never_persisted(self):
        from pending_driver_history_v2 import retain_trucksbook_source_observation
        from unittest.mock import patch
        with patch('pending_driver_history_v2.record_pending_history',AsyncMock()) as store:
            for webhook,guild in [(124,9),(123,10)]:
                result=await retain_trucksbook_source_observation(None,webhook_id=webhook,guild_id=guild,expected_guild_id=9,
                    configured_webhook_id='123',parsed_job={'job_id':'456'},driver_label='redacted')
                self.assertEqual(result['status'],'source_rejected')
        store.assert_not_awaited()
