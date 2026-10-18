import unittest
from datetime import datetime, timedelta, timezone
from unified_reconciliation_policy_v2 import classify_delivery

TIME = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
BASE = {
    "provider": "trucky", "discord_user_id": 123,
    "mileage_type": "real", "miles": "100.00",
    "metadata": {"game": "ets2", "origin": "London", "destination": "Duisburg", "cargo": "Machinery"},
    "delivered_at": TIME,
}


class ReconciliationTests(unittest.TestCase):
    def test_untrusted_discord_job_never_creditable(self):
        for key in ("trusted", "verified_delivery", "eligible_for_award"):
            with self.subTest(key=key):
                event = {**BASE, key: False}
                self.assertEqual(classify_delivery(
                    event, [], provider_watermark=TIME + timedelta(hours=1),
                    provider_feed_complete=True), "pending")

    def test_unknown_candidate_provider_blocks_award(self):
        other = {**BASE, "provider": "unknown"}
        self.assertEqual(classify_delivery(
            BASE, [other], provider_watermark=TIME + timedelta(hours=1),
            provider_feed_complete=True), "pending")

    def test_boolean_discord_id_is_not_driver(self):
        event = {**BASE, "discord_user_id": True}
        self.assertEqual(classify_delivery(
            event, [], provider_watermark=TIME + timedelta(hours=1),
            provider_feed_complete=True), "pending")

    def test_missing_watermark_never_credits(self):
        self.assertEqual(classify_delivery(BASE, []), "pending")

    def test_clock_alone_cannot_prove_provider_completeness(self):
        self.assertEqual(classify_delivery(BASE, [],
            provider_watermark=TIME + timedelta(hours=1)), "pending")

    def test_complete_other_feed_allows_unique_job(self):
        self.assertEqual(classify_delivery(BASE, [],
            provider_watermark=TIME + timedelta(hours=1), provider_feed_complete=True), "creditable")

    def test_incomplete_other_feed_stays_pending(self):
        self.assertEqual(classify_delivery(BASE, [],
            provider_watermark=TIME + timedelta(minutes=20), provider_feed_complete=True), "pending")

    def test_matching_job_similarity_never_confirms_duplicate(self):
        other = {**BASE, "provider": "trucksbook"}
        watermark = TIME + timedelta(hours=1)
        self.assertEqual(classify_delivery(BASE, [other], provider_watermark=watermark), "pending")
        self.assertEqual(classify_delivery(BASE, [{**other, "credited": True}],
            provider_watermark=watermark, provider_feed_complete=True), "pending")

    def test_two_legitimate_identical_route_jobs_remain_pending_for_review(self):
        other = {**BASE, "provider": "trucksbook"}
        self.assertEqual(classify_delivery(BASE, [other, {**other, "credited": True}],
            provider_watermark=TIME + timedelta(hours=1),
            provider_feed_complete=True), "pending")

    def test_verified_shared_id_is_required_to_confirm_a_credited_duplicate(self):
        for incoming_provider, other_provider in (("trucky", "trucksbook"),
                                                   ("trucksbook", "trucky")):
            with self.subTest(incoming=incoming_provider):
                incoming = {**BASE, "provider": incoming_provider,
                            "shared_journey_id": "proof-abc", "shared_id_verified": True}
                other = {**BASE, "provider": other_provider, "credited": True,
                         "shared_journey_id": "proof-abc", "shared_id_verified": True}
                self.assertEqual(classify_delivery(incoming, [other]), "duplicate")

    def test_unverified_or_different_shared_id_stays_pending(self):
        incoming = {**BASE, "shared_journey_id": "proof-abc", "shared_id_verified": True}
        for candidate in (
            {**BASE, "provider": "trucksbook", "credited": True,
             "shared_journey_id": "proof-abc", "shared_id_verified": False},
            {**BASE, "provider": "trucksbook", "credited": True,
             "shared_journey_id": "proof-other", "shared_id_verified": True},
        ):
            self.assertEqual(classify_delivery(incoming, [candidate]), "pending")

    def test_watermark_in_different_timezone(self):
        from datetime import timezone
        watermark = (TIME + timedelta(hours=1)).astimezone(timezone(timedelta(hours=2)))
        self.assertEqual(classify_delivery(BASE, [], provider_watermark=watermark, provider_feed_complete=True), "creditable")

    def test_trucksbook_race_not_eligible(self):
        self.assertEqual(classify_delivery({**BASE, "provider": "trucksbook",
            "mileage_type": "race"}, [], provider_watermark=TIME + timedelta(hours=1)), "pending")

    def test_incomplete_metadata_never_credits(self):
        self.assertEqual(classify_delivery({**BASE, "metadata": {"game": "ets2"}}, [],
            provider_watermark=TIME + timedelta(hours=1), provider_feed_complete=True), "pending")

    def test_invalid_miles_never_credits(self):
        for miles in ("0", "-2", "NaN", "Infinity", None):
            with self.subTest(miles=miles):
                self.assertEqual(classify_delivery({**BASE, "miles": miles}, [],
                    provider_watermark=TIME + timedelta(hours=1), provider_feed_complete=True), "pending")

    def test_missing_driver_identity_never_credits(self):
        self.assertEqual(classify_delivery({**BASE, "discord_user_id": None}, [],
            provider_watermark=TIME + timedelta(hours=1), provider_feed_complete=True), "pending")

    def test_same_provider_candidate_fails_closed(self):
        self.assertEqual(classify_delivery(BASE, [BASE],
            provider_watermark=TIME + timedelta(hours=1), provider_feed_complete=True), "pending")

    def test_different_driver_is_not_duplicate(self):
        other = {**BASE, "provider": "trucksbook", "discord_user_id": 999, "credited": True}
        self.assertEqual(classify_delivery(BASE, [other],
            provider_watermark=TIME + timedelta(hours=1), provider_feed_complete=True), "creditable")


if __name__ == "__main__":
    unittest.main()
