"""Offline tests for Trucky roster response validation. Run: python -m unittest discover -s tests"""
import unittest
from unittest.mock import patch
import trucky_membership_adapter as membership
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

    def test_schema_failures_have_safe_diagnostic_codes(self):
        with self.assertRaises(MembershipUnavailable) as caught:
            validate_roster_page({"data": "not-a-list"}, 1)
        self.assertEqual(caught.exception.diagnostic_code, "invalid_roster_format")


class _ResponseContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *args):
        return False


class _Session:
    def __init__(self, response, **kwargs):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, *args, **kwargs):
        return _ResponseContext(self.response)


class MembershipLookupDiagnostics(unittest.IsolatedAsyncioTestCase):
    async def test_membership_does_not_claim_steam_mismatch_when_provider_omits_proof(self):
        class Response:
            status = 200

            async def json(self, content_type=None):
                return {
                    "current_page": 1,
                    "last_page": 1,
                    "data": [{"id": 123, "name": "Driver", "company_id": 49041}],
                }

        with patch.dict("os.environ", {"TRUCKY_API_TOKEN": "test-token"}), \
             patch.object(membership.aiohttp, "ClientSession",
                          side_effect=lambda **kw: _Session(Response(), **kw)):
            report = await membership.inspect_member_identity_fields("123")
        self.assertTrue(report["found"])
        self.assertIsNone(report["steam_id_verified"])
        self.assertEqual(report["steam_ownership_evidence"],
                         "not_exposed_by_membership_roster")

    async def test_unmatched_account_is_not_reported_as_steam_mismatch(self):
        class Response:
            status = 200

            async def json(self, content_type=None):
                return {"current_page": 1, "last_page": 1, "data": []}

        with patch.dict("os.environ", {"TRUCKY_API_TOKEN": "test-token"}), \
             patch.object(membership.aiohttp, "ClientSession",
                          side_effect=lambda **kw: _Session(Response(), **kw)):
            report = await membership.inspect_member_identity_fields("123")
        self.assertFalse(report["found"])
        self.assertIsNone(report["steam_id_verified"])

    async def test_http_status_is_reported_without_body_or_secret(self):
        class Response:
            status = 404

        with patch.dict("os.environ", {"TRUCKY_API_TOKEN": "never-print-this"}), \
             patch.object(membership.aiohttp, "ClientSession",
                          side_effect=lambda **kw: _Session(Response(), **kw)):
            with self.assertRaises(MembershipUnavailable) as caught:
                await membership.inspect_member_identity_fields("123")
        self.assertEqual(caught.exception.diagnostic_code, "http_404")
        self.assertNotIn("never-print-this", str(caught.exception))



class BulkRosterUserAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_bulk_roster_uses_same_identified_client_as_single_member_lookup(self):
        calls=[]
        class Response:
            status=200
            async def json(self,content_type=None):
                return {'current_page':1,'last_page':1,
                        'data':[{'id':123,'name':'Driver','company_id':49041}]}
        class Session(_Session):
            def get(self,*args,**kwargs):
                calls.append(kwargs)
                return super().get(*args,**kwargs)
        with patch.dict('os.environ',{'TRUCKY_API_TOKEN':'test-only'}), patch.object(
                membership.aiohttp,'ClientSession',side_effect=lambda **kw:Session(Response(),**kw)):
            self.assertEqual(await membership.fetch_current_member_ids(), {'123'})
        self.assertEqual(calls[0]['headers']['User-Agent'],'A&T Transport LTD Command Bot')
        self.assertIs(calls[0]['allow_redirects'],False)
