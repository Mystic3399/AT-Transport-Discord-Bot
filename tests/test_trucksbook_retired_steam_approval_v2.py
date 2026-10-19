"""The unsupported TrucksBook Steam-login flow must never establish ownership."""
from datetime import datetime, timezone
import unittest

from unified_driver_profiles import approve_trucksbook_claim_after_witness
from unified_mileage_engine_control_v2 import verified_job_evidence_reason


class NoDatabaseAccess:
    def acquire(self):
        raise AssertionError("Retired approval must not access the database")


class RetiredTrucksBookApprovalTests(unittest.IsolatedAsyncioTestCase):
    async def test_even_matching_observed_ids_and_digest_cannot_approve(self):
        with self.assertRaisesRegex(ValueError, "pending provider-supported evidence"):
            await approve_trucksbook_claim_after_witness(
                NoDatabaseAccess(), 111, 222, "76561198000000072", "123456",
                "a" * 64, membership_confirmed=True,
            )

    async def test_self_approval_also_fails_before_database_access(self):
        with self.assertRaises(ValueError):
            await approve_trucksbook_claim_after_witness(
                NoDatabaseAccess(), 111, 111, "76561198000000072", "123456",
                "a" * 64, membership_confirmed=True,
            )

    def test_retired_or_email_session_labels_never_authorise_mileage(self):
        job = {
            "provider": "trucksbook", "provider_job_id": "68093174",
            "provider_account_id": "123456", "discord_user_id": 111,
            "provider_authenticated": True, "completed": True,
            "job_status": "completed", "ownership_verified": True,
            "steam_identity_verified": True, "ownership_evidence_sha256": "a" * 64,
            "source_evidence_sha256": "b" * 64, "mileage_type": "real",
            "miles": "176", "delivered_at": datetime.now(timezone.utc),
        }
        for kind in ("trucksbook_steam_authenticated_session", "email_login",
                     "staff_witnessed_email_session", "public_profile_name_match"):
            with self.subTest(kind=kind):
                self.assertEqual(verified_job_evidence_reason({**job, "ownership_evidence_kind": kind}),
                                 "ownership_evidence_kind_unverified")
