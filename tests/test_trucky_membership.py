"""Offline tests for Trucky roster response validation. Run: python -m unittest discover -s tests"""
import unittest
from trucky_membership_adapter import validate_roster_page, MembershipUnavailable

class RosterValidationTests(unittest.TestCase):
    def test_valid_member(self):
        self.assertEqual(validate_roster_page(
            {"current_page": 1, "last_page": 1, "data": [
                {"id": 209123, "name": "Mystic3399", "company_id": 49041}]}, 1)[2][0]["id"], 209123)

    def test_wrong_company_rejected(self):
        with self.assertRaises(MembershipUnavailable):
            validate_roster_page({"current_page": 1, "last_page": 1,
                "data": [{"id": 209123, "name": "Mystic3399", "company_id": 999}]}, 1)

    def test_malformed_member_rejected(self):
        with self.assertRaises(MembershipUnavailable):
            validate_roster_page({"current_page": 1, "last_page": 1,
                "data": [{"id": "209123", "name": "Mystic3399", "company_id": 49041}]}, 1)

    def test_missing_page_rejected(self):
        with self.assertRaises(MembershipUnavailable):
            validate_roster_page({"current_page": 2, "last_page": 2, "data": []}, 1)

    def test_changed_page_count_rejected(self):
        with self.assertRaises(MembershipUnavailable):
            validate_roster_page({"current_page": 2, "last_page": 3, "data": []}, 2, 2)

    def test_empty_roster_valid(self):
        self.assertEqual(validate_roster_page(
            {"current_page": 1, "last_page": 1, "data": []}, 1)[2], [])
