import unittest
from datetime import datetime, timedelta, timezone
from unified_legacy_mileage_bridge_v2 import preview_legacy_real_miles

NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
JOB = {
    "provider": "trucky", "provider_job_id": "t-1",
    "discord_user_id": 123, "mileage_type": "real", "miles": "200",
    "metadata": {"game": "ets2", "origin": "London", "destination": "Duisburg", "cargo": "Machinery"},
    "delivered_at": NOW,
}
READY = {"provider_watermark": NOW + timedelta(hours=1), "provider_feed_complete": True}


class LegacyBridgeTests(unittest.TestCase):
    def test_safe_whole_miles_preview(self):
        result = preview_legacy_real_miles(JOB, [], **READY)
        self.assertEqual(result["status"], "ready_for_atomic_award")
        self.assertEqual(result["legacy_award_miles"], 200)
        self.assertEqual(result["provider_job_key"], ("trucky", "t-1"))

    def test_fractional_miles_never_silently_truncated(self):
        result = preview_legacy_real_miles({**JOB, "miles": "124.27"}, [], **READY)
        self.assertEqual(result["status"], "fractional_miles_pending")
        self.assertEqual(result["legacy_award_miles"], 0)

    def test_no_verified_feed_completeness_means_no_credit(self):
        self.assertEqual(preview_legacy_real_miles(JOB, [],
            provider_watermark=READY["provider_watermark"])["legacy_award_miles"], 0)

    def test_race_miles_not_sent_to_legacy_real_counter(self):
        result = preview_legacy_real_miles({**JOB, "mileage_type": "race"}, [], **READY)
        self.assertEqual(result["status"], "unsupported_race_award")

    def test_existing_cross_provider_award_is_not_duplicated(self):
        other = {**JOB, "provider": "trucksbook", "credited": True}
        self.assertEqual(preview_legacy_real_miles(JOB, [other], **READY)["status"], "duplicate")


if __name__ == "__main__":
    unittest.main()
