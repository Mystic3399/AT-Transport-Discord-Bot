"""Regression tests for cross-provider matching and mileage-credit boundaries."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from automatic_linked_mileage_v2 import matches, same_route_window, trucksbook_comparison
from unified_job_matching_v2 import is_candidate_duplicate


def delivery(provider, *, user=789, miles="100", minute=0, kind="real",
             game="ets2", origin="Paris", destination="Lille", cargo="Food"):
    return {
        "provider": provider,
        "provider_job_id": provider + "-1",
        "discord_user_id": user,
        "mileage_type": kind,
        "miles": Decimal(miles),
        "delivered_at": datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
                        + timedelta(minutes=minute),
        "metadata": {"game": game, "origin": origin,
                     "destination": destination, "cargo": cargo},
    }


class AutomaticMileageMatchingTests(unittest.TestCase):
    def test_same_driver_real_delivery_matches_even_if_distance_differs(self):
        tb = delivery("trucksbook", miles="100")
        tr = delivery("trucky", miles="120")
        self.assertTrue(matches(tb, tr))
        self.assertEqual(max(tb["miles"], tr["miles"]), Decimal("120"))

    def test_same_provider_is_never_cross_provider_duplicate(self):
        self.assertFalse(matches(delivery("trucky"), delivery("trucky")))

    def test_different_drivers_never_merge(self):
        self.assertFalse(matches(delivery("trucky"), delivery("trucksbook", user=790)))

    def test_real_and_race_never_merge(self):
        self.assertFalse(matches(delivery("trucky", kind="race"),
                                 delivery("trucksbook", kind="real")))

    def test_different_cargo_is_not_a_match(self):
        self.assertFalse(matches(delivery("trucky"),
                                 delivery("trucksbook", cargo="Machinery")))

    def test_delivery_time_window_is_bounded(self):
        self.assertTrue(matches(delivery("trucky"),
                                delivery("trucksbook", minute=30)))
        self.assertFalse(matches(delivery("trucky"),
                                 delivery("trucksbook", minute=31)))

    def test_same_route_window_does_not_prove_duplicate_without_cargo(self):
        tr = delivery("trucky")
        tb = delivery("trucksbook", cargo="Machinery")
        self.assertTrue(same_route_window(tr, tb))
        self.assertFalse(is_candidate_duplicate(tr, tb, max_miles_delta=None))

    def test_cancelled_and_unlisted_trucksbook_jobs_are_not_comparable(self):
        for status in ("cancelled", "canceled", "unlisted"):
            with self.subTest(status=status):
                evidence = dict(delivery("trucksbook")["metadata"],
                                stats_type="Real", unit="mi", distance=100,
                                status=status,
                                source_message_at="2026-11-01T12:00:00+00:00")
                with self.assertRaises(ValueError):
                    trucksbook_comparison("tb-1", 789, evidence, 100)

    def test_completed_trucksbook_job_remains_comparable(self):
        evidence = dict(delivery("trucksbook")["metadata"],
                        stats_type="Real", unit="mi", distance=100,
                        status="completed",
                        source_message_at="2026-11-01T12:00:00+00:00")
        result = trucksbook_comparison("tb-1", 789, evidence, 100)
        self.assertEqual(result["miles"], Decimal("100"))

    def test_missing_route_evidence_fails_closed(self):
        tr = delivery("trucky")
        tb = delivery("trucksbook")
        del tb["metadata"]["origin"]
        with self.assertRaises(ValueError):
            matches(tr, tb)


if __name__ == "__main__":
    unittest.main()
