import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from unified_staging_reconciliation_worker_v2 import reconcile_and_stage_miles


class ReconciliationWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._activation = patch.dict(os.environ, {
            "AT_V2_ISOLATED_STAGING": "NO",
            "AT_V2_TEST_DATABASE_DISPOSABLE": "YES",
            "AT_V2_MILEAGE_ENGINE_TEST_ENABLED": "YES",
        }, clear=False)
        self._activation.start()
        self.addCleanup(self._activation.stop)
        self.now = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
        self.incoming = {
            "provider": "trucky", "provider_job_id": "100",
            "provider_account_id": "1234567", "discord_user_id": 123,
            "mileage_type": "real", "miles": "60", "delivered_at": self.now,
            "provider_authenticated": True, "completed": True,
            "job_status": "completed", "ownership_verified": True,
            "steam_identity_verified": True,
            "ownership_evidence_kind": "trucky_signed_job_steam_match",
            "source_evidence_sha256": "a" * 64,
            "metadata": {"game": "ets2", "origin": "London",
                         "destination": "Paris", "cargo": "Food"},
        }
        self.feed = {
            "provider_feed_complete": True,
            "provider_feed_authenticated": True,
            "provider_feed_snapshot_sha256": "b" * 64,
            "provider_watermark": self.now + timedelta(minutes=31),
        }

    async def test_activation_is_disabled_by_default_and_always_off_in_staging(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("unified_staging_reconciliation_worker_v2.award_staging_miles",
                   new_callable=AsyncMock) as award:
            result = await reconcile_and_stage_miles(None, self.incoming, [], **self.feed)
            self.assertEqual(result["status"], "mileage_engine_disabled")
            award.assert_not_awaited()
        with patch.dict(os.environ, {
            "AT_V2_TEST_DATABASE_DISPOSABLE": "YES",
            "AT_V2_MILEAGE_ENGINE_TEST_ENABLED": "YES",
            "AT_V2_MILEAGE_ENGINE_ENABLED": "YES",
        }, clear=True), patch(
            "unified_staging_reconciliation_worker_v2.award_staging_miles",
            new_callable=AsyncMock,
        ) as award:
            award.return_value = {"status": "staging_awarded"}
            result = await reconcile_and_stage_miles(None, self.incoming, [], **self.feed)
            self.assertEqual(result["status"], "staging_awarded")
            award.assert_awaited_once()
        with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "YES"}, clear=False), \
             patch("unified_staging_reconciliation_worker_v2.award_staging_miles",
                   new_callable=AsyncMock) as award:
            result = await reconcile_and_stage_miles(None, self.incoming, [], **self.feed)
            self.assertEqual(result["status"], "mileage_engine_disabled")
            award.assert_not_awaited()

    async def test_quarantined_observation_cannot_award_with_feed_flags(self):
        incoming = {**self.incoming, "verified_delivery": False,
                    "eligible_for_award": False, "trusted": False}
        with patch("unified_staging_reconciliation_worker_v2.award_staging_miles",
                   new_callable=AsyncMock) as award:
            result = await reconcile_and_stage_miles(None, incoming, [], **self.feed)
        self.assertEqual(result["status"], "unverified_observation")
        award.assert_not_awaited()

    async def test_feed_requires_authentication_completeness_watermark_and_digest(self):
        for kwargs in (
            {"provider_feed_complete": True},
            {**self.feed, "provider_feed_authenticated": False},
            {**self.feed, "provider_feed_snapshot_sha256": "bad"},
            {**self.feed, "provider_watermark": None},
        ):
            with self.subTest(kwargs=kwargs):
                with patch("unified_staging_reconciliation_worker_v2.award_staging_miles",
                           new_callable=AsyncMock) as award:
                    result = await reconcile_and_stage_miles(
                        None, self.incoming, [], **kwargs
                    )
                self.assertEqual(result["status"], "pending")
                self.assertEqual(result["reason"], "opposite_provider_feed_unverified")
                award.assert_not_awaited()

    async def test_verified_complete_feed_can_reach_transactional_staging_ledger(self):
        with patch("unified_staging_reconciliation_worker_v2.award_staging_miles",
                   new_callable=AsyncMock) as award:
            award.return_value = {"status": "staging_awarded", "credited_whole_miles": 60}
            result = await reconcile_and_stage_miles(None, self.incoming, [], **self.feed)
        self.assertEqual(result["status"], "staging_awarded")
        self.assertTrue(award.await_args.kwargs["canonical_delivery_key"].startswith("v2:"))

    async def test_candidate_match_stays_pending_without_shared_journey_evidence(self):
        other = {**self.incoming, "provider": "trucksbook", "provider_job_id": "200",
                 "provider_account_id": "7654321", "miles": "60", "credited": True,
                 "ownership_evidence_kind": "trucksbook_official_account_steam_match"}
        incoming = {**self.incoming, "provider_job_id": "101"}
        with patch("unified_staging_reconciliation_worker_v2.award_staging_miles",
                   new_callable=AsyncMock) as award:
            result = await reconcile_and_stage_miles(None, incoming, [other], **self.feed)
        self.assertEqual(result["status"], "pending")
        award.assert_not_awaited()

    async def test_unverified_shared_journey_identity_stays_pending(self):
        with patch("unified_staging_reconciliation_worker_v2.award_staging_miles",
                   new_callable=AsyncMock) as award:
            result = await reconcile_and_stage_miles(
                None, self.incoming, [], shared_journey_id="shared-100", **self.feed
            )
        self.assertEqual(result["reason"], "shared_journey_review_required")
        award.assert_not_awaited()

    async def test_shared_journey_id_requires_auditable_review(self):
        with patch("unified_staging_reconciliation_worker_v2.award_staging_miles",
                   new_callable=AsyncMock) as award:
            result = await reconcile_and_stage_miles(
                None, self.incoming, [], shared_journey_id="shared-100",
                shared_id_verified=True, **self.feed
            )
        self.assertEqual(result["reason"], "shared_journey_review_required")
        award.assert_not_awaited()

    async def test_missing_ownership_provider_or_steam_evidence_fails_closed(self):
        for field, value in (("ownership_verified", False),
                             ("provider_authenticated", False),
                             ("steam_identity_verified", False),
                             ("job_status", "in_progress")):
            with self.subTest(field=field):
                incoming = {**self.incoming, field: value}
                with patch("unified_staging_reconciliation_worker_v2.award_staging_miles",
                           new_callable=AsyncMock) as award:
                    result = await reconcile_and_stage_miles(
                        None, incoming, [], **self.feed
                    )
                self.assertEqual(result["status"], "pending")
                award.assert_not_awaited()

    async def test_public_profile_or_name_is_not_trucksbook_ownership_proof(self):
        incoming = {**self.incoming, "provider": "trucksbook",
                    "ownership_evidence_kind": "public_profile_name_match"}
        with patch("unified_staging_reconciliation_worker_v2.award_staging_miles",
                   new_callable=AsyncMock) as award:
            result = await reconcile_and_stage_miles(None, incoming, [], **self.feed)
        self.assertEqual(result["reason"], "ownership_evidence_kind_unverified")
        award.assert_not_awaited()

    async def test_trucksbook_race_miles_are_excluded(self):
        incoming = {**self.incoming, "provider": "trucksbook", "mileage_type": "race",
                    "ownership_evidence_kind": "trucksbook_official_account_steam_match"}
        with patch("unified_staging_reconciliation_worker_v2.award_staging_miles",
                   new_callable=AsyncMock) as award:
            result = await reconcile_and_stage_miles(None, incoming, [], **self.feed)
        self.assertEqual(result["reason"], "trucksbook_race_excluded")
        award.assert_not_awaited()

    async def test_bad_job_evidence_is_pending_without_award(self):
        incoming = {**self.incoming, "source_evidence_sha256": "not-a-digest"}
        with patch("unified_staging_reconciliation_worker_v2.award_staging_miles",
                   new_callable=AsyncMock) as award:
            result = await reconcile_and_stage_miles(None, incoming, [], **self.feed)
        self.assertEqual(result["reason"], "source_evidence_missing")
        award.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
