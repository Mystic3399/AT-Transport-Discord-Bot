"""Regression tests for A&T coach/car speed exceptions and evidence safety."""
import unittest
from decimal import Decimal
from datetime import datetime, timezone

from world_of_trucks_policy_v2 import (
    authenticated_vehicle_type, classify_job_mileage,
)
from trucky_verified_job_candidate_v2 import normalize_verified_trucky_job
from pending_race_mileage_v2 import merge_history_observations, summarize_pending_trucky_mileage


class VehicleSpeedPolicyTests(unittest.TestCase):
    def classify(self, *, game, vehicle, speed, provider="trucky", stats="Real"):
        return classify_job_mileage(
            provider, stats, game=game, max_speed=speed, speed_unit="mph",
            vehicle_type=vehicle,
        )

    def test_coach_68_mph_inclusive(self):
        for speed in (Decimal("0"), Decimal("62"), Decimal("67.999"), Decimal("68")):
            with self.subTest(speed=speed):
                self.assertEqual(self.classify(game="ETS2", vehicle="coach",
                                               speed=speed, stats="Race").mileage_type, "real")
        for speed in (Decimal("68.0001"), Decimal("69"), Decimal("112")):
            self.assertEqual(self.classify(game="ETS2", vehicle="coach",
                                           speed=speed).mileage_type, "race")
        self.assertIsNone(self.classify(game="ETS2", vehicle="coach", speed=Decimal("112.01")))

    def test_ats_car_85_mph_inclusive(self):
        for speed in (Decimal("81.01"), Decimal("84.9999"), Decimal("85")):
            self.assertEqual(self.classify(game="ATS", vehicle="car",
                                           speed=speed, stats="Race").mileage_type, "real")
        for speed in (Decimal("85.0001"), Decimal("86"), Decimal("112")):
            self.assertEqual(self.classify(game="ATS", vehicle="car", speed=speed).mileage_type, "race")
        self.assertIsNone(self.classify(game="ATS", vehicle="car", speed=Decimal("112.01")))
        exact = Decimal("85") * Decimal("1.609344")
        self.assertEqual(classify_job_mileage(
            "trucky", "Race", game="ATS", max_speed=exact,
            speed_unit="km/h", vehicle_type="car").mileage_type, "real")

    def test_trucks_and_untyped_jobs_are_not_granted_car_coach_exemptions(self):
        self.assertEqual(self.classify(game="ETS2", vehicle="truck", speed=62).mileage_type, "real")
        self.assertEqual(self.classify(game="ETS2", vehicle="truck", speed="62.001").mileage_type, "race")
        self.assertEqual(self.classify(game="ATS", vehicle=None, speed=82).mileage_type, "race")
        self.assertEqual(self.classify(game="ATS", vehicle="truck", speed=82).mileage_type, "race")
        self.assertEqual(self.classify(game="ETS2", vehicle=None, speed=67).mileage_type, "real")

    def test_unauthenticated_category_or_bad_units_never_award(self):
        self.assertIsNone(self.classify(game="ATS", vehicle="unknown", speed=84))
        self.assertIsNone(self.classify(game="ATS", vehicle="coach", speed=60))
        self.assertIsNone(self.classify(game="ETS2", vehicle="car", speed=60))
        self.assertIsNone(classify_job_mileage(
            "trucky", "Race", game="ATS", max_speed=85, vehicle_type="car"))
        self.assertIsNone(self.classify(game="ETS2", vehicle="coach", speed=bool(True)))
        self.assertIsNone(self.classify(game="ETS2", vehicle="coach", speed="NaN"))
        self.assertEqual(authenticated_vehicle_type({"cargo_name":"Cars"}), None)
        self.assertEqual(authenticated_vehicle_type(
            {"vehicle_type":"coach", "vehicle_category":"car"}), "unknown")
        self.assertEqual(authenticated_vehicle_type({"vehicle_type":"passenger_car"}), "car")

    def test_verified_api_vehicle_classification(self):
        job=dict(company_id=49041,id=123,user_id=456,game_id=2,status="completed",
                 completed_at="2026-11-20T12:00:00Z",stats_type="race_miles",
                 driven_distance_km="100",distance_unit="km",
                 max_speed_kmh=str(Decimal("85")*Decimal("1.609344")),
                 vehicle_type="car")
        candidate=normalize_verified_trucky_job(
            job,verified_provider_account_id="456",discord_user_id=789)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["mileage_type"], "real")
        self.assertEqual(candidate["classification_evidence"]["vehicle_type"], "car")
        self.assertFalse(candidate["eligible_for_award"])
        self.assertFalse(candidate["trusted"])
        self.assertIsNone(normalize_verified_trucky_job(
            job,verified_provider_account_id="999",discord_user_id=789))

    def test_historical_vehicle_conflicts_are_held_and_new_drivers_unverified(self):
        evidence=dict(game_id=1,status="completed",stats_type="race_miles",
                      max_speed_kmh=str(Decimal("68")*Decimal("1.609344")),
                      driven_distance_km="120",distance_unit="km",
                      completed_at="2026-11-20T12:00:00Z",vehicle_type="coach")
        row=lambda data: dict(provider="trucky",provider_job_id="123",
                              provider_account_id="456",source_kind="authenticated_api",
                              evidence=data)
        self.assertIsNotNone(merge_history_observations(
            [row({k:v for k,v in evidence.items() if k!="vehicle_type"}),row(evidence)]))
        self.assertIsNone(merge_history_observations(
            [row(evidence),row(dict(evidence,vehicle_type="truck"))]))
        pending=summarize_pending_trucky_mileage([row(evidence)],set(),mileage_type="real")
        self.assertEqual(pending["unverified_jobs"],1)
        self.assertFalse(pending["mileage_awarded"])


if __name__ == "__main__":
    unittest.main()
