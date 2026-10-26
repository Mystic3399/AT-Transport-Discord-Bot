import hashlib
import hmac
import json
import os
import unittest
from unittest.mock import patch, AsyncMock
from aiohttp.test_utils import TestClient, TestServer
from trucky_staging_receiver_v2 import create_staging_app, create_integrations_app


class ReceiverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patcher = patch.dict(os.environ, {"TRUCKY_WEBHOOK_SIGNING_SECRET": "staging-test-secret"})
        self.patcher.start()
        self.client = TestClient(TestServer(create_staging_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.patcher.stop()

    async def test_authenticated_job_is_quarantined(self):
        body = json.dumps({"event": "job_completed", "data": {"id": 1}}).encode()
        signature = hmac.new(b"staging-test-secret", body, hashlib.sha256).hexdigest()
        response = await self.client.post(
            "/staging/trucky/webhook", data=body,
            headers={"X-Signature-SHA256": signature}
        )
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["status"],
                         "authenticated_pending_schema_verification")

    async def test_bad_signature_is_rejected(self):
        response = await self.client.post(
            "/staging/trucky/webhook", data=b'{"event":"job_completed","data":{}}',
            headers={"X-Signature-SHA256": "0" * 64}
        )
        self.assertEqual(response.status, 401)

    async def test_missing_secret_blocks_requests(self):
        with patch.dict(os.environ, {"TRUCKY_WEBHOOK_SIGNING_SECRET": ""}):
            response = await self.client.post("/staging/trucky/webhook", data=b"{}")
        self.assertEqual(response.status, 503)


class _ReceiptConnection:
    def __init__(self):
        self.args = None

    async def fetchrow(self, query, *args):
        self.args = args
        return {"receipt_id": 7}


class _Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *args):
        return False


class _ReceiptPool:
    def __init__(self):
        self.connection = _ReceiptConnection()

    def acquire(self):
        return _Acquire(self.connection)


class IntegratedReceiverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patcher = patch.dict(os.environ, {"TRUCKY_WEBHOOK_SIGNING_SECRET": "staging-test-secret"})
        self.patcher.start()
        self.pool = _ReceiptPool()
        self.client = TestClient(TestServer(create_integrations_app(self.pool)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.patcher.stop()

    async def test_health_and_steam_callback_routes_are_mounted(self):
        health = await self.client.get("/healthz")
        self.assertEqual(health.status, 200)
        callback = await self.client.get("/auth/steam/callback?state=bad")
        self.assertEqual(callback.status, 400)

    async def test_signed_completion_persists_only_minimal_receipt(self):
        payload = {"event": "job_completed", "data": {
            "id": 991234560, "driver": {"steam_profile": {"steam_id": "sensitive"}},
            "planned_distance": 888, "distance_unit": "km"
        }}
        body = json.dumps(payload).encode()
        signature = hmac.new(b"staging-test-secret", body, hashlib.sha256).hexdigest()
        response = await self.client.post(
            "/trucky/webhook", data=body,
            headers={"X-Signature-SHA256": signature})
        self.assertEqual(response.status, 202)
        self.assertEqual(await response.json(), {
            "status": "authenticated_pending_api_verification",
            "receipt_recorded": True, "mileage_awarded": False,
            "identity_status": "pending_identity_verification",
            "identity_reason": "provider_steam_evidence_missing_or_invalid"})
        digest, event, candidate = self.pool.connection.args
        self.assertEqual(digest, hashlib.sha256(body).hexdigest())
        self.assertEqual(event, "job_completed")
        self.assertEqual(candidate, "991234560")
        self.assertNotIn("sensitive", repr(self.pool.connection.args))
        self.assertNotIn("888", repr(self.pool.connection.args))

    async def test_signed_completion_checks_roster_and_links_matching_steam(self):
        user_id = 987654321012345678
        body = json.dumps({"event": "job_completed", "data": {
            "id": 27805272,
            "user_id": 123456,
            "completed_at": "2026-10-17T20:00:00Z",
            "driver": {"id": 123456,
                       "steam_profile": {"steam_id": "76561198000000000"}},
        }}).encode()
        signature = hmac.new(b"staging-test-secret", body, hashlib.sha256).hexdigest()
        callback = AsyncMock()
        with patch("trucky_membership_adapter.verify_member", new=AsyncMock(return_value="A&T Driver")) as roster, \
             patch("unified_driver_profiles.record_trucky_webhook_receipt", new=AsyncMock(return_value=7)), \
             patch("unified_driver_profiles.auto_approve_trucky_if_steam_matches", new=AsyncMock(return_value=user_id)) as link, \
             patch("unified_driver_profiles.attest_trucky_receipt_identity", new=AsyncMock(return_value=True)) as attest:
            app = create_integrations_app(self.pool, on_trucky_linked=callback)
            client = TestClient(TestServer(app))
            await client.start_server()
            try:
                with patch("builtins.print") as log:
                    response = await client.post(
                        "/trucky/webhook", data=body,
                        headers={"X-Signature-SHA256": signature},
                    )
                safe_log = "\n".join(
                    str(call.args[0]) for call in log.call_args_list
                    if call.args
                )
                self.assertIn(
                    "TRUCKY_IDENTITY_VERIFICATION event=job_completed "
                    "outcome=identity_linked receipt_recorded=yes "
                    "active_member=yes steam_match=yes identity_reason=none mileage_awarded=no",
                    safe_log,
                )
                for private_value in (
                    "76561198000000000", "123456", str(user_id)
                ):
                    self.assertNotIn(private_value, safe_log)
                self.assertEqual(response.status, 202)
                self.assertEqual((await response.json())["identity_status"], "identity_linked")
                self.assertIsNone((await response.json())["identity_reason"])
            finally:
                await client.close()
            roster.assert_awaited_once_with("123456")
            self.assertEqual(link.await_args.kwargs["provider_steam_id"], "76561198000000000")
            self.assertTrue(link.await_args.kwargs["trusted_provider_source"])
            self.assertTrue(link.await_args.kwargs["membership_confirmed"])
            callback.assert_awaited_once_with(user_id)

    async def test_steam_mismatch_is_distinguished_from_missing_evidence(self):
        body = json.dumps({"event": "job_completed", "data": {
            "id": 27805274,
            "completed_at": "2026-10-17T20:00:00Z",
            "driver": {"id": 123456,
                       "steam_profile": {"steam_id": "76561198000000000"}},
        }}).encode()
        signature = hmac.new(b"staging-test-secret", body, hashlib.sha256).hexdigest()
        mismatch = ValueError("safe mismatch")
        mismatch.code = "steam_identity_mismatch"
        with patch("trucky_membership_adapter.verify_member", new=AsyncMock(return_value="A&T Driver")), \
             patch("unified_driver_profiles.record_trucky_webhook_receipt", new=AsyncMock(return_value=9)), \
             patch("unified_driver_profiles.auto_approve_trucky_if_steam_matches", new=AsyncMock(side_effect=mismatch)):
            with patch("builtins.print") as log:
                response = await self.client.post(
                    "/trucky/webhook", data=body,
                    headers={"X-Signature-SHA256": signature},
                )
        response_body = await response.json()
        self.assertEqual(response_body["identity_status"], "identity_not_verified")
        self.assertEqual(response_body["identity_reason"], "steam_identity_mismatch")
        lines = "\n".join(str(call.args[0]) for call in log.call_args_list if call.args)
        self.assertIn("steam_match=no identity_reason=steam_identity_mismatch", lines)
        self.assertNotIn("76561198000000000", lines)

    async def test_signed_completion_without_active_roster_member_stays_unlinked(self):
        body = json.dumps({"event": "job_completed", "data": {
            "id": 27805273,
            "user_id": 123456,
            "completed_at": "2026-10-17T20:00:00Z",
            "driver": {"id": 123456,
                       "steam_profile": {"steam_id": "76561198000000000"}},
        }}).encode()
        signature = hmac.new(b"staging-test-secret", body, hashlib.sha256).hexdigest()
        with patch("trucky_membership_adapter.verify_member", new=AsyncMock(return_value=None)), \
             patch("unified_driver_profiles.record_trucky_webhook_receipt", new=AsyncMock(return_value=8)), \
             patch("unified_driver_profiles.auto_approve_trucky_if_steam_matches", new=AsyncMock()) as link:
            response = await self.client.post(
                "/trucky/webhook", data=body,
                headers={"X-Signature-SHA256": signature},
            )
        self.assertEqual(response.status, 202)
        self.assertEqual((await response.json())["identity_status"], "not_active_company_member")
        link.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

