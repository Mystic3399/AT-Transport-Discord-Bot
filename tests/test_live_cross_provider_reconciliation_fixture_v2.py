"""Regression contract for the 2026-10-18 redacted dual-provider test.

The fixture deliberately records only what was established from owner-supplied
tracker observations and safe Railway logs. It must not be upgraded into an
awardable provider record without authoritative feed evidence.
"""
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from unified_job_matching_v2 import is_candidate_duplicate
from unified_reconciliation_policy_v2 import classify_delivery
from unified_canonical_journey_v2 import canonical_delivery_key

FIXTURE = Path(__file__).parent / "fixtures" / "cross_provider_delivery_2026-10-18.json"


def _incomplete_record(provider, job_id, source):
    # No provider identity, authoritative delivery time, game, or full verified
    # job metadata is present in the actual staging evidence.
    return {
        "provider": provider,
        "provider_job_id": job_id,
        "discord_user_id": None,
        "mileage_type": "real",
        "miles": source["distance"],
        "delivered_at": source.get("provider_delivery_timestamp"),
        "metadata": {
            "origin": source["origin"],
            "destination": source["destination"],
            "cargo": source["cargo"],
        },
        "trusted": False,
        "verified_delivery": False,
        "eligible_for_award": False,
    }


class LiveCrossProviderFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.tb = cls.fixture["trucksbook"]
        cls.trucky = cls.fixture["trucky"]

    def test_fixture_preserves_only_observed_evidence(self):
        self.assertFalse(self.fixture["authoritative_provider_records_retrieved"])
        self.assertIsNone(self.fixture["verified_shared_journey_id"])
        self.assertIsNone(self.tb["provider_delivery_timestamp"])
        self.assertFalse(self.tb["provider_account_ownership_verified"])
        self.assertTrue(self.tb["company_export_uploaded"])
        self.assertEqual(self.tb["company_export_rows"], 263)
        self.assertEqual(self.tb["matching_export_rows"], 1)
        self.assertFalse(self.tb["stable_driver_account_id_in_export"])
        self.assertIsNone(self.tb["completion_timezone"])
        self.assertEqual(self.tb["duration_seconds"], 719)
        self.assertTrue(self.trucky["authoritative_job_record_retrieved"])
        self.assertTrue(self.trucky["api_driver_account_id_unambiguous"])
        self.assertFalse(self.trucky["api_steam_identity_present"])
        self.assertTrue(self.trucky["api_completion_timestamp_available"])
        self.assertEqual(self.trucky["api_completed_at_utc"], "2026-10-18T20:29:36Z")
        self.assertEqual(self.trucky["api_real_distance_km"], "284")
        self.assertEqual(self.trucky["api_real_driving_time_seconds"], 725)
        self.assertTrue(self.trucky["signed_receipt_hmac_verified"])
        self.assertTrue(self.trucky["account_link_matches_verified_steam"])
        self.assertEqual(self.tb["provider_job_id"], "68093174")
        self.assertEqual(self.trucky["provider_job_id"], "27848310")

    def test_actual_delivery_stays_unmatched_without_authoritative_identity(self):
        tb = _incomplete_record("trucksbook", self.tb["provider_job_id"], self.tb)
        trucky = _incomplete_record("trucky", self.trucky["provider_job_id"], self.trucky)
        with self.assertRaises((ValueError, KeyError)):
            is_candidate_duplicate(trucky, tb)
        self.assertEqual(classify_delivery(trucky, [tb]), "pending")

    def test_actual_delivery_cannot_reach_award_gate_even_with_feed_claim(self):
        trucky = _incomplete_record("trucky", self.trucky["provider_job_id"], self.trucky)
        complete_watermark = datetime(2026, 10, 18, 22, tzinfo=timezone.utc)
        self.assertEqual(classify_delivery(
            trucky, [], provider_feed_complete=True,
            provider_watermark=complete_watermark,
        ), "pending")

    def test_provider_ids_remain_separate_without_verified_shared_id(self):
        tb_key = canonical_delivery_key("trucksbook", self.tb["provider_job_id"])
        trucky_key = canonical_delivery_key("trucky", self.trucky["provider_job_id"])
        self.assertNotEqual(tb_key, trucky_key)
        self.assertIsNone(self.fixture["verified_shared_journey_id"])

    def test_observed_repeat_ids_are_stable_but_cross_provider_ids_do_not_merge(self):
        self.assertEqual(
            canonical_delivery_key("trucksbook", self.tb["provider_job_id"]),
            canonical_delivery_key("trucksbook", self.tb["provider_job_id"]),
        )
        self.assertEqual(
            canonical_delivery_key("trucky", self.trucky["provider_job_id"]),
            canonical_delivery_key("trucky", self.trucky["provider_job_id"]),
        )
        self.assertNotEqual(
            canonical_delivery_key("trucksbook", self.tb["provider_job_id"]),
            canonical_delivery_key("trucky", self.trucky["provider_job_id"]),
        )

    def test_tracker_fields_match_but_are_not_identity_evidence(self):
        self.assertEqual(self.tb["origin"], self.trucky["origin"])
        self.assertEqual(self.tb["destination"], self.trucky["destination"])
        self.assertEqual(self.tb["cargo"], self.trucky["cargo"])
        self.assertEqual(self.tb["truck"], self.trucky["truck"])
        self.assertEqual(self.tb["distance"], self.trucky["distance"])
        self.assertEqual(self.tb["classification"], self.trucky["classification"])
        self.assertEqual(self.tb["driver_label"], "redacted")
        self.assertNotIn("discord_user_id", self.tb)

    def test_real_export_and_authenticated_trucky_job_are_candidates_only(self):
        from decimal import Decimal
        trucky_miles = Decimal(self.trucky["api_real_distance_km"]) * Decimal("0.621371")
        self.assertEqual(trucky_miles.quantize(Decimal("0.01")), Decimal("176.47"))
        self.assertEqual(Decimal(self.tb["distance"]), Decimal("176"))
        self.assertEqual(abs(self.tb["duration_seconds"] -
                             self.trucky["api_real_driving_time_seconds"]), 6)
        # The export timezone is unknown. A displayed 22:29 cannot be compared
        # to Trucky's 20:29:36Z until its TrucksBook timezone is established.
        self.assertIsNone(self.tb["completion_timezone"])
        self.assertIsNone(self.fixture["verified_shared_journey_id"])


if __name__ == "__main__":
    unittest.main()
