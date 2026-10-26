"""Historical candidates never change totals or authenticate display-name claims."""
from copy import deepcopy
from decimal import Decimal
import unittest
from unittest.mock import AsyncMock

from historical_mileage_preview_v2 import build_historical_preview, read_historical_snapshot


def snapshot():
    return {'drivers':[{'discord_user_id':123,'real_miles':88942}],
            'processed_jobs':[{'job_id':'68248029','discord_user_id':123,'accepted_distance':365,'statistics':'Real'}],
            'links':[{'provider':p,'provider_account_id':a,'discord_user_id':123,'verified':True}
                     for p,a in [('trucksbook','111'),('trucky','222')]]}


def row(provider='trucky', job='28021177', **changes):
    data={'provider':provider,'provider_job_id':job,'provider_account_id':'222' if provider=='trucky' else '111',
          'statistics':'Real','distance':'586' if provider=='trucky' else '365', 'unit':'km' if provider=='trucky' else 'mi',
          'delivered_at':'2026-10-26T19:50:23Z',
          'metadata':{'game':'ETS2','origin':'test-origin','destination':'Praha','cargo':'Medical Equipment'}}
    return {**data,**changes}


class HistoricalPreviewTests(unittest.TestCase):
    def test_live_distances_are_candidates_not_a_second_award(self):
        source=snapshot();before=deepcopy(source)
        report=build_historical_preview(source,[row('trucksbook','68248029'),row()])
        self.assertEqual(source,before)
        self.assertEqual(report['candidate_pairs'][0]['decision'],'potential_duplicate')
        self.assertFalse(report['candidate_pairs'][0]['authoritative_match'])
        self.assertEqual(report['records'][0]['decision'],'already_recorded')
        self.assertEqual(report['records'][1]['reason'],'potential_cross_provider_duplicate')
        self.assertEqual(Decimal(report['records'][1]['miles']),Decimal('364.123518651077724'))
        self.assertEqual(report['drivers'][0]['proposed_real_total'],'88942')
        self.assertEqual(report['drivers'][0]['approved_additions'],'0')
        self.assertFalse(report['award_authorized'])

    def test_absent_historical_job_does_not_mean_uncredited(self):
        report=build_historical_preview(snapshot(),[row('trucksbook','999')])
        self.assertEqual(report['records'][0]['reason'],'historical_coverage_and_provider_evidence_required')
        self.assertEqual(report['drivers'][0]['unexplained_balance'],'88577')

    def test_same_provider_replays_collapsed_conflicts_held(self):
        repeated=build_historical_preview(snapshot(),[row(),row()])
        self.assertEqual((repeated['unique_records'],repeated['replays']),(1,1))
        conflicting=build_historical_preview(snapshot(),[row(),row(distance='635')])
        self.assertEqual(conflicting['records'][0]['reason'],'conflicting_provider_records')

    def test_claims_and_names_are_not_verified_ownership(self):
        data=snapshot();data['links'][1]['verified']=False
        self.assertEqual(build_historical_preview(data,[row()])['records'][0]['reason'],'account_ownership_pending')
        for changes in ({'provider_account_id':'unknown','driver_name':'Mystic3399'}, {'discord_user_id':456}):
            report=build_historical_preview(snapshot(),[row(**changes)])
            self.assertEqual(report['records'][0]['decision'],'pending')
            self.assertFalse(report['award_authorized'])

    def test_invalid_units_timezones_and_distances_held(self):
        for changes in ({'unit':None},{'delivered_at':'2026-10-26T19:50:23'}, {'distance':'NaN'},
                        {'distance':True}, {'distance':'-1'}, {'statistics':'WoTr'}, {'provider_job_id':'x'}):
            with self.subTest(changes=changes):
                self.assertEqual(build_historical_preview(snapshot(),[row(**changes)])['records'][0]['decision'],'pending')

    def test_race_policy_separate_and_wotr_real_accepted(self):
        report=build_historical_preview(snapshot(),[row(statistics='Race'),row('trucksbook','999',statistics='Race'),
                                                    row('trucksbook','998',statistics='Real, WoTr')])
        self.assertEqual(report['records'][0]['mileage_type'],'race')
        self.assertEqual(report['records'][1]['reason'],'excluded_or_ambiguous_classification')
        self.assertEqual(report['records'][2]['mileage_type'],'real')
        self.assertEqual(report['drivers'][0]['approved_additions'],'0')

    def test_similar_jobs_from_different_drivers_not_matched(self):
        data=snapshot();data['links'][1]['discord_user_id']=456
        report=build_historical_preview(data,[row('trucksbook','68248029'),row()])
        self.assertEqual(report['candidate_pairs'],[])

    def test_historical_driver_conflict_preserves_totals(self):
        data=snapshot();data['processed_jobs'][0]['discord_user_id']=456
        report=build_historical_preview(data,[row('trucksbook','68248029')])
        self.assertEqual(report['records'][0]['reason'],'historical_driver_conflict')
        self.assertEqual(report['records'][0]['decision'],'pending')

    def test_missing_metadata_never_proves_independence(self):
        report=build_historical_preview(snapshot(),[row('trucksbook','68248029'),row(metadata=None)])
        self.assertEqual(report['candidate_pairs'],[])
        self.assertEqual(report['records'][1]['reason'],'comparison_metadata_incomplete')

    def test_repeated_preview_is_deterministic_and_no_write_api_exists(self):
        source=snapshot();records=[row()]
        self.assertEqual(build_historical_preview(source,records),build_historical_preview(source,records))


class Scope:
    def __init__(self,value):self.value=value
    async def __aenter__(self):return self.value
    async def __aexit__(self,*args):pass


class HistoricalSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_enforces_readonly_repeatable_read(self):
        from datetime import datetime,timezone
        connection=type('Connection',(),{})()
        connection.fetch=AsyncMock(return_value=[])
        connection.fetchval=AsyncMock(return_value=datetime(2026,10,26,tzinfo=timezone.utc))
        calls=[]
        def transaction(**kwargs):calls.append(kwargs);return Scope(None)
        connection.transaction=transaction
        pool=type('Pool',(),{'acquire':lambda self:Scope(connection)})()
        result=await read_historical_snapshot(pool)
        self.assertEqual(calls,[{'isolation':'repeatable_read','readonly':True}])
        self.assertEqual(result['processed_jobs'],[])
        self.assertTrue(all(call.args[0].strip().startswith('SELECT') for call in connection.fetch.await_args_list))
