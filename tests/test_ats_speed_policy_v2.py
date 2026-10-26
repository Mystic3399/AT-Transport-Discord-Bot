import unittest
from decimal import Decimal
from world_of_trucks_policy_v2 import classify_job_mileage
from historical_mileage_preview_v2 import build_historical_preview
from tests.test_historical_mileage_preview_v2 import row,snapshot

class ATSSpeedPolicyTests(unittest.TestCase):
    def test_ats_thresholds_both_providers(self):
        for provider in ('trucksbook','trucky'):
            for speed in (0,80,81):
                with self.subTest(provider=provider,speed=speed):
                    self.assertEqual(classify_job_mileage(provider,'Race',game='ATS',max_speed=speed,speed_unit='mph').mileage_type,'real')
            for speed in (82,100,112):
                result=classify_job_mileage(provider,'Real',game='ATS',max_speed=speed,speed_unit='mph')
                self.assertEqual(result.mileage_type if result else None,'race' if provider=='trucky' else None)

    def test_no_rounding_or_inferred_units(self):
        for value,unit in [('81.5','mph'),('81.999','mph'),('112.001','mph'),(113,'mph'),(130,None),('NaN','mph'),(-1,'mph'),(True,'mph')]:
            self.assertIsNone(classify_job_mileage('trucky','Real',game='ATS',max_speed=value,speed_unit=unit))
        for mph in (81,82):
            self.assertEqual(classify_job_mileage('trucky','Real',game='ATS',max_speed=Decimal(mph)*Decimal('1.609344'),speed_unit='km/h').mileage_type,'real' if mph==81 else 'race')

    def test_ets2_unchanged_and_invalid_types_never_reclassified(self):
        self.assertEqual(classify_job_mileage('trucky','Race',game='ETS2',max_speed=60,speed_unit='mph').mileage_type,'race')
        self.assertEqual(classify_job_mileage('trucksbook','Real, WoTr',game='ETS2',max_speed=90,speed_unit='mph').mileage_type,'real')
        for stat in ('invalid','road_trip_real_miles','WoTr','Real, Race'):
            self.assertIsNone(classify_job_mileage('trucky',stat,game='ATS',max_speed=60,speed_unit='mph'))

    def test_preview_uses_ats_policy_without_awarding(self):
        report=build_historical_preview(snapshot(),[row(game='ATS',max_speed=82,speed_unit='mph')])
        self.assertEqual(report['records'][0]['mileage_type'],'race')
        self.assertFalse(report['award_authorized'])
        self.assertEqual(report['drivers'][0]['proposed_real_total'],'88942')
