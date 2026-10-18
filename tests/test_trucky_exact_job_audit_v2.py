"""Safety tests for the staging-only exact Trucky job audit."""
import unittest

from trucky_jobs_api_v2 import find_job_record, safe_job_record_report


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
        self.assertIn("driver.steam_profile.steam_id", report["field_types"])


if __name__ == "__main__":
    unittest.main()
