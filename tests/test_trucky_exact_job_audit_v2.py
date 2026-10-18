"""Safety tests for the staging-only exact Trucky job audit."""
import unittest

from trucky_jobs_api_v2 import (
    find_job_record, inspect_job_identity_fields, safe_job_record_report,
)


class ExactJobLookupTests(unittest.TestCase):
    def test_unique_id_field_match_returns_record_and_path(self):
        payload = {"data": [
            {"id": 11, "driver": {"id": 9922, "steam_profile": {"steam_id": "76561198000000000"}}},
            {"id": 27848310, "status": "completed", "distance": 176.25},
        ]}
        result = find_job_record(payload, "27848310")
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["id_paths"], ["id"])
        self.assertEqual(result["record"]["distance"], 176.25)

    def test_no_match_and_ambiguous_matches_fail_closed(self):
        self.assertEqual(find_job_record({"data": [{"id": 1}]}, 2)["status"], "not_found")
        self.assertEqual(find_job_record({"data": [{"id": 2}, {"id": "2"}]}, 2)["status"], "ambiguous")
        self.assertEqual(find_job_record({"data": [{"id": 1, "driver": {"id": 2}}]}, 2)["status"], "not_found")

    def test_response_shape_without_record_array_is_not_interpreted(self):
        result = find_job_record({"items": [{"id": 2}]}, 2)
        self.assertEqual(result["status"], "unsupported_response_shape")

    def test_bad_job_ids_rejected(self):
        for value in (0, -1, " 2", "2.0", True, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                find_job_record({"data": []}, value)

    def test_report_keeps_identifiers_private_and_only_emits_safe_job_fields(self):
        record = {
            "id": 27848310,
            "status": "completed",
            "completed_at": "2026-10-18T20:29:00Z",
            "distance_km": 283.24,
            "distance_unit": "km",
            "mileage_type": "real",
            "driver": {"id": 987654, "name": "PRIVATE", "steam_profile": {"steam_id": "76561198000000000"}},
        }
        report = safe_job_record_report(record)
        rendered = str(report)
        self.assertNotIn("987654", rendered)
        self.assertNotIn("PRIVATE", rendered)
        self.assertNotIn("76561198000000000", rendered)
        self.assertNotIn("27848310", rendered)
        self.assertEqual(report["safe_delivery_fields"]["distance_km"], 283.24)
        self.assertEqual(report["safe_delivery_fields"]["distance_unit"], "km")
        self.assertEqual(report["safe_delivery_fields"]["mileage_type"], "real")
        self.assertEqual(
            report["identity_field_presence"]["steam_identity_paths"],
            ["steam_profile.steam_id"],
        )

    def test_driver_steam_identity_is_extracted_but_never_reported(self):
        record = {"driver": {"id": 987, "steam_profile": {"steam_id": "76561198000000000"}}}
        result = inspect_job_identity_fields(record)
        self.assertEqual(result["account_id"], "987")
        self.assertEqual(result["steam_id"], "76561198000000000")
        self.assertEqual(result["safe_report"]["driver_account_id_paths"], ["id"])
        self.assertTrue(result["safe_report"]["valid_steam_identity_present"])
        self.assertNotIn("987", str(result["safe_report"]))
        self.assertNotIn("76561198000000000", str(result["safe_report"]))

    def test_ambiguous_driver_and_steam_id_fields_are_not_selected(self):
        result = inspect_job_identity_fields({"driver": {
            "id": 1, "account_id": 2,
            "steam_id": "76561198000000000",
            "steam_profile": {"steam_id": "76561198000000001"},
        }})
        self.assertIsNone(result["account_id"])
        self.assertIsNone(result["steam_id"])
        self.assertFalse(result["safe_report"]["driver_account_id_unambiguous"])
        self.assertFalse(result["safe_report"]["steam_identity_unambiguous"])


if __name__ == "__main__":
    unittest.main()
