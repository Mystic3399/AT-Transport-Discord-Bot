import hashlib
import hmac
import json
import os
import unittest
from unittest.mock import patch
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
            "receipt_recorded": True, "mileage_awarded": False})
        digest, event, candidate = self.pool.connection.args
        self.assertEqual(digest, hashlib.sha256(body).hexdigest())
        self.assertEqual(event, "job_completed")
        self.assertEqual(candidate, "991234560")
        self.assertNotIn("sensitive", repr(self.pool.connection.args))
        self.assertNotIn("888", repr(self.pool.connection.args))


if __name__ == "__main__":
    unittest.main()
