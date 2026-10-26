import unittest
from trucky_verified_job_candidate_v2 import (normalize_verified_trucky_job,
    review_trucky_job_against_other_provider)

BASE = {"company_id": 49041, "id": 27805272, "user_id": 1234,
        "status": "completed", "completed_at": "2026-10-17T12:00:00Z",
        "stats_type": "real_miles", "distance_unit": "km",
        "driven_distance_km": 120.5}


class VerifiedJobCandidateTests(unittest.TestCase):
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
        self.assertIsNone(self.candidate({**BASE, "event_tag": "WoTr", "stats_type": "race_miles"}))

    def test_race_rejected(self):
        self.assertIsNone(self.candidate({**BASE, "stats_type": "race_miles"}))

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

