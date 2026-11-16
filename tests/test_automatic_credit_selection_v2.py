import unittest
from datetime import datetime, timedelta, timezone
from unified_reconciliation_policy_v2 import automatic_credit_selection


class AutomaticSelectionTests(unittest.TestCase):
    def setUp(self):
        self.job = dict(provider="trucky", discord_user_id=42, mileage_type="real",
                        miles="260", delivered_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
                        metadata=dict(game="ats", origin="Seattle", destination="Brownsville",
                                      cargo="Air Conditioning"))
        self.other = dict(self.job, provider="trucksbook", miles="200")
        self.complete = dict(provider_feed_complete=True,
                             provider_watermark=self.job["delivered_at"]+timedelta(hours=1))

    def test_higher_wins_in_both_arrival_orders(self):
        for first, second in ((self.job, self.other), (self.other, self.job)):
            result = automatic_credit_selection(first, [second], **self.complete)
            self.assertEqual(result["status"], "matched")
            self.assertEqual(result["winner"]["miles"], "260")
            self.assertEqual(result["suppressed"]["miles"], "200")

    def test_equal_mileage_keeps_trucksbook(self):
        result = automatic_credit_selection(self.job, [dict(self.other, miles="260")],
                                            **self.complete)
        self.assertEqual(result["winner"]["provider"], "trucksbook")

    def test_incomplete_feed_and_ambiguous_route_wait(self):
        self.assertEqual(automatic_credit_selection(self.job, [self.other])["status"], "pending")
        self.assertEqual(automatic_credit_selection(
            self.job, [self.other, self.other], **self.complete)["status"], "pending")

    def test_different_route_is_independent_only_with_complete_feed(self):
        other = dict(self.other, metadata=dict(self.other["metadata"], destination="Omak"))
        self.assertEqual(automatic_credit_selection(self.job, [other], **self.complete)["status"],
                         "independent")
        self.assertEqual(automatic_credit_selection(self.job, [other])["status"], "pending")
