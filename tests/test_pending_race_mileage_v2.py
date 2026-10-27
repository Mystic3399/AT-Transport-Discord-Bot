import unittest
from decimal import Decimal
from pending_race_mileage_v2 import summarize_pending_race,merge_history_observations
from unified_distance_units import KM_TO_MILES

def row(job='123',account='456',**changes):
    evidence=dict(game_id=1,status='completed',stats_type='race_miles',driven_distance_km=100,
                  distance_unit='km',completed_at='2026-10-19T12:00:00Z')
    evidence.update(changes)
    return dict(provider='trucky',provider_job_id=job,provider_account_id=account,
                source_kind='authenticated_api',evidence=evidence)

class RaceInventoryTests(unittest.TestCase):
    def test_verified_race_retained_with_exact_decimal_precision_and_no_credit(self):
        result=summarize_pending_race([row(driven_distance_km='0.125'),row('124',driven_distance_km='0.125')],{'456'})
        self.assertEqual(result['verified_pending_jobs'],2)
        self.assertEqual(result['verified_pending_miles'],Decimal('0.25')*KM_TO_MILES)
        self.assertEqual(result['ready_to_award'],0);self.assertFalse(result['mileage_awarded'])

    def test_replays_and_export_observations_do_not_double_pending_miles(self):
        record=row()
        result=summarize_pending_race([record,record,{**record,'source_kind':'owner_export'}],{'456'})
        self.assertEqual(result['verified_pending_jobs'],1)
        self.assertEqual(result['verified_pending_miles'],100*KM_TO_MILES)

    def test_changed_distance_account_or_classification_is_held(self):
        for conflict in (row(driven_distance_km=101),row(account='999'),row(stats_type='real_miles')):
            result=summarize_pending_race([row(),conflict],{'456','999'})
            self.assertEqual(result['conflicting_jobs'],1)
            self.assertEqual(result['verified_pending_miles'],0)

    def test_added_explicit_speed_does_not_make_unchanged_evidence_conflicting(self):
        old=row(game_id=2);new=row(game_id=2,max_speed_kmh=132)
        result=summarize_pending_race([old,new],{'456'})
        self.assertEqual(result['verified_pending_jobs'],1)
        self.assertEqual(result['conflicting_jobs'],0)
        self.assertIsNone(merge_history_observations([new,row(game_id=2,max_speed_kmh=129)]))

    def test_ats_policy_uses_explicit_kmh_and_not_display_speed_or_stats_alone(self):
        records=[row('123',game_id=2,max_speed_kmh=132,stats_type='real_miles',max_speed=1),
                 row('124',game_id=2,max_speed_kmh=129),row('125',game_id=2,max_speed=132),
                 row('126',game_id=2,max_speed_kmh=131),row('127',game_id=2,max_speed_kmh=181)]
        result=summarize_pending_race(records,{'456'})
        self.assertEqual(result['verified_pending_jobs'],1)
        self.assertEqual(result['classification_pending_jobs'],3)

    def test_unregistered_accounts_are_stored_separately_without_inventing_ownership(self):
        result=summarize_pending_race([row()],set())
        self.assertEqual(result['unverified_jobs'],1)
        self.assertEqual(result['unverified_miles'],100*KM_TO_MILES)
        self.assertEqual(result['verified_pending_jobs'],0)

    def test_credited_jobs_and_other_drivers_are_not_missing_again(self):
        result=summarize_pending_race([row(),row('124','999')],{'456','999'},credited_jobs={'123'},target_accounts={'456'})
        self.assertEqual(result['already_credited_jobs'],1)
        self.assertEqual(result['verified_pending_miles'],0)
        self.assertEqual(result['unverified_jobs'],0)

    def test_real_and_trucksbook_race_and_invalid_markets_are_never_trucky_race(self):
        records=[row(stats_type='real_miles'),{**row('124'),'provider':'trucksbook'},
                 row('125',stats_type='road_trip_race_miles'),row('126',stats_type='invalid')]
        self.assertEqual(summarize_pending_race(records,{'456'})['verified_pending_jobs'],0)

    def test_invalid_units_distances_or_completion_times_remain_held(self):
        for changes in ({'distance_unit':'mi'},{'driven_distance_km':True},{'driven_distance_km':'NaN'},
                {'driven_distance_km':0},{'completed_at':None},{'completed_at':123},
                {'completed_at':'2026-10-19T12:00:00'},{'completed_at':'2099-01-01T00:00:00Z'}):
            result=summarize_pending_race([row(**changes)],{'456'})
            self.assertEqual(result['verified_pending_miles'],0)
            self.assertEqual(result['classification_pending_jobs'],1)
