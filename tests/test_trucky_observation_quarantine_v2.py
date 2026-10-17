import unittest
from datetime import datetime, timezone
from trucky_observation_quarantine_v2 import quarantine_trucky_observation


class QuarantineTests(unittest.TestCase):
    def setUp(self):
        self.event = {
            "provider": "trucky", "provider_job_id": "27805272",
            "display_name_unverified": "QUIETGHOST",
            "distance": "207.61", "distance_unit": "km",
            "mileage_type": "real", "trusted": False,
        }
        self.now = datetime(2026, 10, 17, 17, 11, tzinfo=timezone.utc)

    def observe(self):
        return quarantine_trucky_observation(
            self.event, discord_message_id=123456, observed_at=self.now
        )

    def test_converts_km_without_awarding(self):
        event = self.observe()
        self.assertEqual(event["miles"], "129.00")
        self.assertIsNone(event["discord_user_id"])
        self.assertFalse(event["verified_delivery"])
        self.assertFalse(event["provider_feed_complete"])
        self.assertFalse(event["eligible_for_award"])

    def test_race_stays_race(self):
        self.event["mileage_type"] = "race"
        self.assertEqual(self.observe()["mileage_type"], "race")

    def test_rejects_claimed_trust(self):
        self.event["trusted"] = True
        with self.assertRaises(ValueError):
            self.observe()

    def test_rejects_unverified_zero_distance(self):
        self.event["distance"] = "0"
        with self.assertRaises(ValueError):
            self.observe()

    def test_rejects_boolean_message_id(self):
        with self.assertRaises(ValueError):
            quarantine_trucky_observation(
                self.event, discord_message_id=True, observed_at=self.now
            )

    def test_rejects_missing_driver_name(self):
        self.event["display_name_unverified"] = ""
        with self.assertRaises(ValueError):
            self.observe()

    def test_rejects_unrecognised_provider(self):
        self.event["provider"] = "trucksbook"
        with self.assertRaises(ValueError):
            self.observe()

    def test_requires_timezone(self):
        with self.assertRaises(ValueError):
            quarantine_trucky_observation(
                self.event, discord_message_id=123456,
                observed_at=datetime(2026, 10, 17)
            )


if __name__ == "__main__":
    unittest.main()
