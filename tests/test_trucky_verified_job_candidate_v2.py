import unittest
from trucky_verified_job_candidate_v2 import normalize_verified_trucky_job

BASE = {"company_id": 49041, "id": 27805272, "user_id": 1234,
        "status": "completed", "completed_at": "2026-10-17T12:00:00Z",
        "stats_type": "real_miles", "distance_unit": "km",
        "real_driven_distance_km": 120.5}


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

    def test_race_rejected(self):
        self.assertIsNone(self.candidate({**BASE, "stats_type": "race_miles"}))

    def test_zero_distance_rejected(self):
        self.assertIsNone(self.candidate({**BASE, "real_driven_distance_km": 0}))

    def test_missing_distance_rejected(self):
        self.assertIsNone(self.candidate({**BASE, "real_driven_distance_km": None}))

    def test_wrong_units_rejected(self):
        self.assertIsNone(self.candidate({**BASE, "distance_unit": "mi"}))


if __name__ == "__main__":
    unittest.main()
