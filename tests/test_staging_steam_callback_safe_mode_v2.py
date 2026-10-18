"""Steam callback cannot persist registrations in isolated acceptance mode."""
import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch
from aiohttp.test_utils import TestClient, TestServer

from trucky_staging_receiver_v2 import create_integrations_app


class StagingSteamCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_staging_callback_rejects_before_database_access(self):
        pool = type("Pool", (), {"acquire": AsyncMock()})()
        app = create_integrations_app(pool)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "YES"}, clear=False):
                response = await client.get(
                    "/auth/steam/callback?state=" + "a" * 48
                )
            self.assertEqual(response.status, 503)
            pool.acquire.assert_not_awaited()
        finally:
            await client.close()


if __name__ == "__main__":
    unittest.main()
