import unittest
from historical_provider_exports_v2 import trucksbook_export_row,trucky_export_row

TB={'Name':'redacted','Game':'ATS','From':'origin','To':'destination','Cargo':'cargo','Accepted distance':'1 234 mi',
    'Maximal reached speed':'81 mph','TrucksBookID':'123','Date':'2026-10-26','Time':'20:50','Job Type':'Standard job'}
TR={'#':456,'Last Update':'2026-10-26T19:50:23','Started at':'2026-10-26T19:16:17','Game':'ATS','Driver':'redacted',
    'Status':'completed','Source City':'origin','Destination City':'destination','Cargo':'cargo','Distance (km)':586,
    'Max Speed':130,'Stats Type':'real_miles','Timezone':'Europe/London','Company ID':49041,'Distance Unit':'mi'}

class ExportAdapterTests(unittest.TestCase):
    def test_tb_explicit_speed_unit_and_distance(self):
        row=trucksbook_export_row(TB)
        self.assertEqual((row['distance'],row['unit'],row['company_policy_classification']),('1234','mi','real'))
        self.assertIsNone(row['provider_account_id']);self.assertFalse(row['ownership_verified'])
        self.assertIsNone(row['delivered_at'])
        self.assertIsNone(trucksbook_export_row({**TB,'Maximal reached speed':'82 mph'})['company_policy_classification'])

    def test_ets2_job_type_does_not_invent_real_classification(self):
        row=trucksbook_export_row({**TB,'Game':'ETS2'})
        self.assertIsNone(row['company_policy_classification'])
        self.assertIn('ets2_provider_classification_missing',row['evidence_issues'])

    def test_trucky_distance_header_not_display_preference_speed_unit_unknown(self):
        row=trucky_export_row(TR)
        self.assertEqual((row['distance'],row['unit']),('586','km'))
        self.assertIsNone(row['speed_unit']);self.assertIsNone(row['company_policy_classification'])
        self.assertIn('max_speed_unit_unverified',row['evidence_issues'])
        self.assertIsNone(row['delivered_at'])

    def test_road_trips_invalid_and_wrong_company_remain_untrusted(self):
        for kind in ('road_trip_real_miles','road_trip_race_miles','invalid'):
            row=trucky_export_row({**TR,'Stats Type':kind,'Game':'ETS2'})
            self.assertIsNone(row['company_policy_classification'])
            self.assertIn('unsupported_provider_classification',row['evidence_issues'])
        with self.assertRaises(ValueError):trucky_export_row({**TR,'Company ID':999})

    def test_ets2_provider_classification_preserved(self):
        self.assertEqual(trucky_export_row({**TR,'Game':'ETS2','Stats Type':'race_miles'})['company_policy_classification'],'race')

    def test_missing_or_changed_schema_and_units_fail_closed(self):
        for row in ({}, {**TB,'Accepted distance':'1234'}, {**TB,'Maximal reached speed':'81'}):
            with self.assertRaises(ValueError):trucksbook_export_row(row)
