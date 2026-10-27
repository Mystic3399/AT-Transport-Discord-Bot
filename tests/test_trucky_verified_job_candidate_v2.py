import unittest
from trucky_verified_job_candidate_v2 import (normalize_verified_trucky_job,
    review_trucky_job_against_other_provider)

BASE = {"company_id": 49041, "game_id": 1, "id": 27805272, "user_id": 1234,
        "status": "completed", "completed_at": "2026-10-17T12:00:00Z",
        "stats_type": "real_miles", "distance_unit": "km",
        "driven_distance_km": 120.5}


class VerifiedJobCandidateTests(unittest.TestCase):
    def test_redacted_authenticated_ats_field_samples_remain_non_awardable(self):
        import json
        from pathlib import Path
        evidence=json.loads(Path('tests/fixtures/trucky_ats_schema_redacted.json').read_text())
        for sample,kind in zip(evidence['samples'],('race','real')):
            candidate=self.candidate({**BASE,**sample})
            self.assertEqual(candidate['mileage_type'],kind)
            self.assertFalse(candidate['trusted'])
            self.assertFalse(candidate['eligible_for_award'])

    def candidate(self, job, account="1234"):
        return normalize_verified_trucky_job(job, verified_provider_account_id=account,
                                             discord_user_id=999)

    def test_valid_candidate_is_not_awardable(self):
        candidate = self.candidate(BASE)
        self.assertIsNotNone(candidate)
        self.assertFalse(candidate["trusted"])
        self.assertFalse(candidate["eligible_for_award"])
        self.assertEqual(candidate["kilometers"], 120.5)

    def test_wrong_account_rejected(self):
        self.assertIsNone(self.candidate(BASE, "9999"))

    def test_wrong_company_rejected(self):
        self.assertIsNone(self.candidate({**BASE, "company_id": 3}))

    def test_incomplete_rejected(self):
        self.assertIsNone(self.candidate({**BASE, "status": "in_progress"}))

    def test_wotr_real_candidate_remains_review_only(self):
        candidate = self.candidate({**BASE, "event_tag": "WoTr"})
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["mileage_type"], "real")
        self.assertFalse(candidate["eligible_for_award"])

    def test_unknown_event_tag_requires_review(self):
        self.assertIsNone(self.candidate({**BASE, "event_tag": "unrecognized"}))

    def test_wotr_race_not_reclassified_as_real(self):
        candidate=self.candidate({**BASE, "event_tag": "WoTr", "stats_type": "race_miles"})
        self.assertEqual(candidate['mileage_type'],'race')
        self.assertFalse(candidate['eligible_for_award'])

    def test_ets2_race_retained_in_separate_review_bucket(self):
        candidate=self.candidate({**BASE, "stats_type": "race_miles"})
        self.assertEqual(candidate['mileage_type'],'race')
        self.assertEqual(candidate['classification_evidence']['policy'],'ets2_provider_classification')

    def test_authenticated_ats_speed_samples_and_exact_boundaries(self):
        from decimal import Decimal
        for kph,expected in [('129','real'),('132','race'),
                (str(Decimal(81)*Decimal('1.609344')),'real'),
                (str(Decimal(82)*Decimal('1.609344')),'race'),
                (str(Decimal(112)*Decimal('1.609344')),'race')]:
            with self.subTest(kph=kph):
                candidate=self.candidate({**BASE,'game_id':2,'game':{'id':2,'code':'ATS'},'max_speed_kmh':kph})
                self.assertEqual(candidate['mileage_type'],expected)
                self.assertEqual(candidate['classification_evidence']['max_speed_kmh'],kph)
                self.assertFalse(candidate['eligible_for_award'])

    def test_ats_provider_label_does_not_override_company_speed_policy(self):
        self.assertEqual(self.candidate({**BASE,'game_id':2,'max_speed_kmh':129,'stats_type':'race_miles'})['mileage_type'],'real')
        self.assertEqual(self.candidate({**BASE,'game_id':2,'max_speed_kmh':132})['mileage_type'],'race')

    def test_ats_missing_unit_specific_speed_and_fractional_gap_stay_pending(self):
        from decimal import Decimal
        for speed in (None,True,-1,'nan',str(Decimal('81.5')*Decimal('1.609344')),
                      str(Decimal('112.1')*Decimal('1.609344'))):
            self.assertIsNone(self.candidate({**BASE,'game_id':2,'max_speed':129,'max_speed_kmh':speed}))
        for statistics in ('none','invalid','road_trip_real','road_trip_race'):
            self.assertIsNone(self.candidate({**BASE,'game_id':2,'max_speed_kmh':129,'stats_type':statistics}))

    def test_missing_unknown_or_conflicting_game_stays_pending(self):
        for fields in ({'game_id':None},{'game_id':True},{'game_id':3},
                       {'game_id':2,'game':{'id':1,'code':'ETS2'}},
                       {'game_id':1,'game':{'id':1,'code':'ATS'}}):
            self.assertIsNone(self.candidate({**BASE,**fields}))

    def test_zero_distance_rejected(self):
        self.assertIsNone(self.candidate({**BASE, "driven_distance_km": 0}))

    def test_missing_distance_rejected(self):
        self.assertIsNone(self.candidate({**BASE, "driven_distance_km": None}))

    def test_unparseable_timestamp_rejected(self):
        self.assertIsNone(self.candidate({**BASE, "completed_at": "not-a-timestamp"}))

    def test_naive_timestamp_rejected(self):
        self.assertIsNone(self.candidate({**BASE, "completed_at": "2026-10-17T12:00:00"}))

    def test_future_timestamp_rejected(self):
        self.assertIsNone(self.candidate({**BASE, "completed_at": "2099-10-17T12:00:00Z"}))

    def test_cross_provider_review_remains_non_awardable(self):
        review = review_trucky_job_against_other_provider(
            BASE, verified_provider_account_id="1234", discord_user_id=999,
            other_provider_jobs=[], provider_feed_complete=False)
        self.assertEqual(review["status"], "pending")
        self.assertFalse(review["awardable"])

    def test_cross_provider_wrong_owner_rejected(self):
        review = review_trucky_job_against_other_provider(
            BASE, verified_provider_account_id="4567", discord_user_id=999,
            other_provider_jobs=[], provider_feed_complete=False)
        self.assertEqual(review["status"], "rejected")
        self.assertFalse(review["awardable"])

    def test_wrong_units_rejected(self):
        self.assertIsNone(self.candidate({**BASE, "distance_unit": "mi"}))


if __name__ == "__main__":
    unittest.main()

