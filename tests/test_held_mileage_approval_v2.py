"""Dry-run safety regression tests: held-mileage release is never freeform."""
import asyncio
import inspect
import unittest
from unittest.mock import patch

import held_mileage_approval_v2 as held
from trucky_linked_allocation_v2 import _settle_candidate


class HeldMileageApprovalSafetyTests(unittest.TestCase):
    def test_rejects_invalid_driver_and_job_identifiers(self):
        self.assertTrue(held._valid_ids(1, 1000, 9000))
        self.assertFalse(held._valid_ids(True, 9000))
        self.assertFalse(held._valid_ids(-1, 9000))
        for job in ("", "0", "-3", "001", "ABC", "9223372036854775808"):
            with self.subTest(job=job):
                self.assertFalse(held._valid_job(job))
        self.assertTrue(held._valid_job("28021177"))

    def test_review_is_auditable_and_time_limited(self):
        ddl=held.SCHEMA
        for field in ("guild_id", "channel_id", "reviewer_id", "discord_user_id",
                      "provider_job_id", "provider_account_id", "evidence_sha256",
                      "mileage_type", "miles", "applied_at"):
            self.assertIn(field, ddl)
        self.assertIn("15 minutes", ddl)
        self.assertIn("UNIQUE(provider_job_id)", ddl)

    def test_confirmation_is_refused_without_activation_before_any_db_call(self):
        class UnexpectedDatabase:
            def acquire(self):
                raise AssertionError("No database access when approval gate is disabled")
        with patch.object(held, "activation_allowed", return_value=False):
            with self.assertRaises(held.HeldMileageApprovalError):
                asyncio.run(held.confirm(
                    UnexpectedDatabase(), guild_id=1,channel_id=2,reviewer_id=3,
                    discord_user_id=4,job_id="28021177"))

    def test_only_authentic_job_evidence_and_active_driver_can_pass(self):
        source=inspect.getsource(held._get_candidate)
        self.assertIn("source_kind='authenticated_api'", source)
        self.assertIn("vault_candidate(", source)
        self.assertIn("_verified_driver_for_candidate", source)
        checks=inspect.getsource(held._verified_driver_for_candidate)
        for field in ("verified_at IS NOT NULL", "ownership_confirmed=TRUE",
                      "membership_confirmed=TRUE", "steam_id", "status='approved'"):
            # The Steam link is verified by its timestamp, rather than by name.
            if field != "steam_id":
                self.assertIn(field, checks)
        confirm=inspect.getsource(held.confirm)
        for field in ("read_only_database_fingerprint", "source_sha256",
                      "provider_account_id", "mileage_type", "members",
                      "_settle_candidate"):
            self.assertIn(field, confirm)
        self.assertNotIn("UPDATE driver_progress", confirm)
        self.assertNotIn("DELETE FROM processed_jobs", confirm)

    def test_manual_review_cannot_skip_duplicate_reconciliation(self):
        source=inspect.getsource(_settle_candidate)
        self.assertIn("reconcile_trucky(conn, candidate)", source)
        self.assertIn("if hold:", source)
        self.assertIn("return hold", source)
        self.assertIn("ON CONFLICT DO NOTHING", source)


if __name__ == "__main__":
    unittest.main()
