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

    def test_fractional_miles_are_carried_without_loss(self):
        first = preview_legacy_real_miles({**JOB, "miles": "0.65"}, [],
            fractional_carry="0", **READY)
        self.assertEqual(first["legacy_award_miles"], 0)
        self.assertEqual(first["fractional_carry_after"], "0.65")
        second = preview_legacy_real_miles({**JOB, "miles": "0.55"}, [],
            fractional_carry=first["fractional_carry_after"], **READY)
        self.assertEqual(second["legacy_award_miles"], 1)
        self.assertEqual(second["fractional_carry_after"], "0.20")

    def test_invalid_fractional_carry_fails_closed(self):
        self.assertEqual(preview_legacy_real_miles(JOB, [],
            fractional_carry="1.5", **READY)["status"], "pending")

    def test_no_verified_feed_completeness_means_no_credit(self):
        self.assertEqual(preview_legacy_real_miles(JOB, [],
            provider_watermark=READY["provider_watermark"])["legacy_award_miles"], 0)

    def test_race_miles_not_sent_to_legacy_real_counter(self):
        result = preview_legacy_real_miles({**JOB, "mileage_type": "race"}, [], **READY)
        self.assertEqual(result["status"], "unsupported_race_award")

    def test_similar_cross_provider_job_is_pending_without_shared_identity(self):
        other = {**JOB, "provider": "trucksbook", "credited": True}
        result = preview_legacy_real_miles(JOB, [other], **READY)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["legacy_award_miles"], 0)

    def test_verified_shared_credited_journey_is_not_duplicated(self):
        incoming = {**JOB, "shared_journey_id": "verified-t1",
                    "shared_id_verified": True}
        other = {**JOB, "provider": "trucksbook", "credited": True,
                 "shared_journey_id": "verified-t1", "shared_id_verified": True}
        result = preview_legacy_real_miles(incoming, [other], **READY)
        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(result["legacy_award_miles"], 0)


if __name__ == "__main__":
    unittest.main()
