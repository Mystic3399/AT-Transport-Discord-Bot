import hashlib
import hmac
import json
import os
import unittest
from unittest.mock import patch
from aiohttp.test_utils import TestClient, TestServer
from trucky_staging_receiver_v2 import create_staging_app


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


if __name__ == "__main__":
    unittest.main()
