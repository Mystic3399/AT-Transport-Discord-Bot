"""Conservative Trucky/TrucksBook cross-provider matching tests."""
import unittest
from datetime import datetime, timedelta, timezone

from unified_job_matching_v2 import is_candidate_duplicate

BASE = {
    "provider": "trucky",
    "discord_user_id": 123,
    "mileage_type": "real",
    "metadata": {"game": "ets2", "origin": "London", "destination": "Duisburg", "cargo": "Machinery"},
    "delivered_at": datetime(2026, 10, 17, 12, tzinfo=timezone.utc),
    "miles": "200.00",
}


class MatchingTests(unittest.TestCase):
    def test_cross_provider_candidate(self):
        self.assertTrue(is_candidate_duplicate(BASE, {**BASE, "provider": "trucksbook",
            "delivered_at": BASE["delivered_at"] + timedelta(minutes=15),
            "miles": "202.00"}))

    def test_not_same_provider_or_different_driver(self):
        self.assertFalse(is_candidate_duplicate(BASE, dict(BASE)))
        self.assertFalse(is_candidate_duplicate(BASE, {**BASE, "provider": "trucksbook",
            "discord_user_id": 124}))

    def test_mismatch_and_time_window(self):
        for changed in (
            {"mileage_type": "race"},
            {"metadata": {**BASE["metadata"], "destination": "Berlin"}},
            {"delivered_at": BASE["delivered_at"] + timedelta(hours=2)},
            {"miles": "230.00"},
        ):
            with self.subTest(changed=changed):
                self.assertFalse(is_candidate_duplicate(
                    BASE, {**BASE, "provider": "trucksbook", **changed}
                ))

    def test_missing_metadata_fails_closed(self):
        with self.assertRaises(ValueError):
            is_candidate_duplicate(BASE, {**BASE, "provider": "trucksbook",
                "metadata": {"game": "ets2"}})


if __name__ == "__main__":
    unittest.main()
