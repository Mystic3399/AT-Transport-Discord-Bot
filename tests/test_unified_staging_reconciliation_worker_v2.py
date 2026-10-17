import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from unified_staging_reconciliation_worker_v2 import reconcile_and_stage_real_miles


class ReconciliationWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
        self.incoming = {
            "provider": "trucky", "provider_job_id": "t-100",
            "discord_user_id": 123, "mileage_type": "real",
            "miles": "60", "delivered_at": self.now,
            "metadata": {"game": "ets2", "origin": "London",
                         "destination": "Paris", "cargo": "Food"},
        }

    async def test_quarantined_observation_cannot_award_with_feed_flags(self):
        self.incoming.update({
            "verified_delivery": False,
            "eligible_for_award": False,
            "trusted": False,
        })
        with patch("unified_staging_reconciliation_worker_v2.award_staging_real_miles",
                   new_callable=AsyncMock) as award:
            result = await reconcile_and_stage_real_miles(
                None, self.incoming, [],
                provider_feed_complete=True,
                provider_watermark=self.now + timedelta(minutes=31),
            )
            self.assertEqual(result["status"], "unverified_observation")
            award.assert_not_awaited()

    async def test_no_feed_proof_cannot_award(self):
        with patch("unified_staging_reconciliation_worker_v2.award_staging_real_miles",
                   new_callable=AsyncMock) as award:
            result = await reconcile_and_stage_real_miles(None, self.incoming, [])
            self.assertEqual(result["status"], "pending")
            award.assert_not_awaited()

    async def test_complete_feed_allows_staging_only(self):
        with patch("unified_staging_reconciliation_worker_v2.award_staging_real_miles",
                   new_callable=AsyncMock) as award:
            award.return_value = {"status": "staging_awarded", "credited_whole_miles": 60}
            result = await reconcile_and_stage_real_miles(
                None, self.incoming, [],
                provider_feed_complete=True,
                provider_watermark=self.now + timedelta(minutes=31),
            )
            self.assertEqual(result["status"], "staging_awarded")
            self.assertEqual(award.await_args.kwargs["canonical_delivery_key"],
                             "provider:trucky:t-100")

    async def test_unverified_shared_identity_never_awards(self):
        with patch("unified_staging_reconciliation_worker_v2.award_staging_real_miles",
                   new_callable=AsyncMock) as award:
            with self.assertRaises(ValueError):
                await reconcile_and_stage_real_miles(
                    None, self.incoming, [], provider_feed_complete=True,
                    provider_watermark=self.now + timedelta(minutes=31),
                    shared_journey_id="shared-100",
                )
            award.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
