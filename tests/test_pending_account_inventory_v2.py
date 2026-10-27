import unittest
from decimal import Decimal
from pending_race_mileage_v2 import build_pending_account_inventory,summarize_pending_trucky_mileage
from unified_distance_units import KM_TO_MILES


def job(id='123', account='456', **changes):
    evidence=dict(game_id=1,status='completed',stats_type='real_miles',driven_distance_km='0.125',
                  distance_unit='km',completed_at='2026-10-19T12:00:00Z')
    evidence.update(changes)
    return dict(provider='trucky',provider_job_id=id,provider_account_id=account,
                source_kind='authenticated_api',evidence=evidence)


class PendingAccountInventoryTests(unittest.TestCase):
    def test_one_account_keeps_real_and_race_separate_and_counts_retries_once(self):
        a=job();b=job('124',stats_type='race_miles')
        report=build_pending_account_inventory([a,a,b],[])
        self.assertEqual(len(report['accounts']),1)
        row=report['accounts'][0]
        self.assertEqual(row['authenticated_jobs'],2)
        self.assertEqual(Decimal(row['pending_real_miles']),Decimal('.125')*KM_TO_MILES)
        self.assertEqual(Decimal(row['pending_race_miles']),Decimal('.125')*KM_TO_MILES)
        self.assertEqual(row['approved_addition'],'0')
        self.assertFalse(report['mileage_awarded'])
        self.assertFalse(report['registration_changed'])

    def test_export_name_never_assigns_discord_identity(self):
        exported={**job(),'source_kind':'owner_export','provider_account_id':None,
                  'evidence':{'candidate_driver_label':'Example Driver'}}
        row=build_pending_account_inventory([job(),exported],[] )['accounts'][0]
        self.assertEqual(row['candidate_labels'],['Example Driver'])
        self.assertIsNone(row['linked_discord_user_id'])
        self.assertEqual(row['link_status'],'account_link_required')
        self.assertTrue(build_pending_account_inventory([job(),exported],[])['labels_are_not_ownership_evidence'])

    def test_existing_exact_provider_link_is_displayed_but_not_job_approval(self):
        links=[dict(provider='trucky',provider_account_id='456',discord_user_id=991)]
        row=build_pending_account_inventory([job()],links)['accounts'][0]
        self.assertEqual(row['linked_discord_user_id'],991)
        self.assertEqual(row['link_status'],'linked_job_verification_pending')
        self.assertEqual(row['approved_addition'],'0')

    def test_other_provider_id_is_not_a_trucky_owner(self):
        row=build_pending_account_inventory([job()],
            [dict(provider='trucksbook',provider_account_id='456',discord_user_id=991)])['accounts'][0]
        self.assertIsNone(row['linked_discord_user_id'])

    def test_conflicting_saved_owners_are_held(self):
        row=build_pending_account_inventory([job()],
            [dict(provider='trucky',provider_account_id='456',discord_user_id=user) for user in (991,992)])['accounts'][0]
        self.assertEqual(row['link_status'],'conflicting_saved_links')
        self.assertIsNone(row['linked_discord_user_id'])

    def test_credited_and_conflicting_jobs_do_not_reappear_as_additions(self):
        report=build_pending_account_inventory([job(),job('124'),job('124',driven_distance_km=999)],[],credited_jobs={'123'})
        row=report['accounts'][0]
        self.assertEqual(row['pending_real_jobs'],0)
        self.assertEqual(row['conflicting_jobs'],1)
        self.assertEqual(row['approved_addition'],'0')

    def test_target_filter_does_not_fall_back_to_name_matching(self):
        self.assertEqual(build_pending_account_inventory([job()],[],target_accounts=set())['accounts'],[])
        self.assertEqual(len(build_pending_account_inventory([job(),job('125','789')],[],target_accounts={'456'})['accounts']),1)

    def test_ats_real_race_and_unknown_speeds_are_kept_separate(self):
        report=build_pending_account_inventory([job(game_id=2,max_speed_kmh=129),
            job('124',game_id=2,max_speed_kmh=132),job('125',game_id=2)],[])
        row=report['accounts'][0]
        self.assertEqual(row['pending_real_jobs'],1)
        self.assertEqual(row['pending_race_jobs'],1)
        self.assertEqual(row['excluded_or_pending_jobs'],1)

    def test_road_trip_and_invalid_units_do_not_get_promoted(self):
        report=build_pending_account_inventory([job(stats_type='road_trip_real_miles'),
            job('124',distance_unit='mi')],[])
        row=report['accounts'][0]
        self.assertEqual(row['pending_real_jobs'],0)
        self.assertEqual(row['excluded_or_pending_jobs'],2)
        with self.assertRaises(ValueError):
            summarize_pending_trucky_mileage([],set(),mileage_type='anything')


class PendingAccountInventoryReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_database_inventory_uses_readonly_snapshot_and_never_writes(self):
        from unittest.mock import AsyncMock, MagicMock
        from pending_race_mileage_v2 import read_pending_account_inventory
        connection=MagicMock()
        connection.fetchval=AsyncMock(side_effect=[True,True])
        connection.fetch=AsyncMock(side_effect=[
            [job()], [dict(provider='trucky',provider_account_id='456',discord_user_id=991)],
            [dict(provider_job_id='123')]])
        connection.execute=AsyncMock()
        transaction=MagicMock()
        transaction.__aenter__=AsyncMock()
        transaction.__aexit__=AsyncMock(return_value=False)
        connection.transaction.return_value=transaction
        pool=MagicMock()
        pool.acquire.return_value.__aenter__=AsyncMock(return_value=connection)
        pool.acquire.return_value.__aexit__=AsyncMock(return_value=False)
        result=await read_pending_account_inventory(pool,991)
        connection.transaction.assert_called_once_with(isolation='repeatable_read',readonly=True)
        connection.execute.assert_not_called()
        self.assertEqual(result['accounts'][0]['pending_real_jobs'],0)
        self.assertFalse(result['mileage_awarded'])
        self.assertTrue(all(call.args[0].lstrip().startswith('SELECT') for call in connection.fetch.call_args_list))

    async def test_invalid_driver_is_rejected_before_database_access(self):
        from unittest.mock import MagicMock
        from pending_race_mileage_v2 import read_pending_account_inventory
        pool=MagicMock()
        for value in (True,0,-1,'991'):
            with self.assertRaises(ValueError):
                await read_pending_account_inventory(pool,value)
        pool.acquire.assert_not_called()
