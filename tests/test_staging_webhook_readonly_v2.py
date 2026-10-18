"""Staging webhook paths are authenticated but never award or mutate identities."""
import ast
import hashlib
import hmac
import json
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer
from trucky_staging_receiver_v2 import create_integrations_app

ROOT = Path(__file__).resolve().parents[1]
SECRET = "stage-only-test-secret"
BODY = json.dumps({
    "event": "job_completed",
    "data": {
        "id": 27805272,
        "user_id": 123456,
        "completed_at": "2026-10-18T18:30:00Z",
        "driver": {
            "id": 123456,
            "steam_profile": {"steam_id": "76561198000000000"},
        },
    },
}).encode()


def signature(body=BODY):
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


class FakePool:
    def acquire(self):
        raise AssertionError("Staging identity audit must use the mocked read-only adapters")


class ReadOnlyTruckyReceiverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict(os.environ, {
            "AT_V2_ISOLATED_STAGING": "YES",
            "TRUCKY_WEBHOOK_SIGNING_SECRET": SECRET,
        }, clear=False)
        self.env.start()
        self.callback = AsyncMock()
        self.client = TestClient(TestServer(create_integrations_app(
            FakePool(), on_trucky_linked=self.callback
        )))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.env.stop()

    async def test_signed_completion_checks_read_only_identity_without_mutations(self):
        with patch("unified_driver_profiles.record_trucky_webhook_receipt",
                   new=AsyncMock(return_value=7)) as receipt, \
             patch("unified_driver_profiles.verify_trucky_event_against_verified_links",
                   new=AsyncMock(return_value={
                       "status": "identity_verified_read_only",
                       "discord_user_id": 987654321,
                   })) as identity_check, \
             patch("unified_driver_profiles.auto_approve_trucky_if_steam_matches",
                   new=AsyncMock()) as auto_approve, \
             patch("trucky_membership_adapter.verify_member",
                   new=AsyncMock(return_value="Verified member")):
            response = await self.client.post(
                "/trucky/webhook", data=BODY,
                headers={"X-Signature-SHA256": signature()},
            )
            result = await response.json()

        self.assertEqual(response.status, 202)
        self.assertEqual(result["status"], "authenticated_observation_only")
        self.assertEqual(result["identity_status"], "identity_verified_read_only")
        self.assertTrue(result["receipt_recorded"])
        self.assertFalse(result["mileage_awarded"])
        self.assertFalse(result["registration_changed"])
        self.assertFalse(result["roles_changed"])
        receipt.assert_awaited_once()
        identity_check.assert_awaited_once()
        auto_approve.assert_not_awaited()
        self.callback.assert_not_awaited()
        self.assertNotIn("76561198000000000", json.dumps(result))
        self.assertNotIn("123456", json.dumps(result))

    async def test_duplicate_signed_delivery_is_idempotent_and_still_read_only(self):
        with patch("unified_driver_profiles.record_trucky_webhook_receipt",
                   new=AsyncMock(side_effect=[7, None])) as receipt, \
             patch("unified_driver_profiles.verify_trucky_event_against_verified_links",
                   new=AsyncMock(return_value={
                       "status": "identity_verified_read_only",
                       "discord_user_id": 987654321,
                   })), \
             patch("unified_driver_profiles.auto_approve_trucky_if_steam_matches",
                   new=AsyncMock()) as auto_approve, \
             patch("trucky_membership_adapter.verify_member",
                   new=AsyncMock(return_value="Verified member")):
            responses = []
            for _ in range(2):
                response = await self.client.post(
                    "/trucky/webhook", data=BODY,
                    headers={"X-Signature-SHA256": signature()},
                )
                responses.append((response.status, await response.json()))

        self.assertEqual([status for status, _ in responses], [202, 202])
        self.assertTrue(responses[0][1]["receipt_recorded"])
        self.assertFalse(responses[1][1]["receipt_recorded"])
        self.assertTrue(all(not item["mileage_awarded"] for _, item in responses))
        self.assertEqual(receipt.await_count, 2)
        auto_approve.assert_not_awaited()
        self.callback.assert_not_awaited()

    async def test_invalid_signature_malformed_body_and_unexpected_event_do_not_write(self):
        with patch("unified_driver_profiles.record_trucky_webhook_receipt",
                   new=AsyncMock()) as receipt, \
             patch("unified_driver_profiles.verify_trucky_event_against_verified_links",
                   new=AsyncMock()) as identity_check, \
             patch("trucky_membership_adapter.verify_member",
                   new=AsyncMock()) as membership:
            denied = await self.client.post(
                "/trucky/webhook", data=BODY,
                headers={"X-Signature-SHA256": "0" * 64},
            )
            malformed_body = b"not-json"
            malformed = await self.client.post(
                "/trucky/webhook", data=malformed_body,
                headers={"X-Signature-SHA256": signature(malformed_body)},
            )
            other_body = json.dumps({"event": "member_updated", "data": {}}).encode()
            unexpected = await self.client.post(
                "/trucky/webhook", data=other_body,
                headers={"X-Signature-SHA256": signature(other_body)},
            )

        self.assertEqual(denied.status, 401)
        self.assertEqual(malformed.status, 401)
        self.assertEqual(unexpected.status, 200)
        self.assertEqual((await unexpected.json())["status"], "ignored_event")
        receipt.assert_not_awaited()
        identity_check.assert_not_awaited()
        membership.assert_not_awaited()
        self.callback.assert_not_awaited()


class AutomaticStagingRoleAndHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(source)

    def _load(self, function_name, namespace):
        node = next(
            item for item in self.tree.body
            if isinstance(item, ast.AsyncFunctionDef) and item.name == function_name
        )
        node.decorator_list = []
        exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), namespace)
        return namespace[function_name]

    def test_automatic_role_and_achievement_handlers_return_in_staging(self):
        async def run():
            divider_sync = AsyncMock()
            db_pool = type("Pool", (), {"acquire": AsyncMock()})()
            ns = {"os": os, "GUILD_ID": 100,
                  "sync_member_role_category_dividers": divider_sync,
                  "db_pool": db_pool}
            member_update = self._load("on_member_update", ns)
            member_remove = self._load("on_member_remove", ns)
            with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "YES"}, clear=False):
                await member_update(None, None)
                await member_remove(None)
            divider_sync.assert_not_awaited()
            db_pool.acquire.assert_not_awaited()

        import asyncio
        asyncio.run(run())


class StartupBackfillSafeModeTests(unittest.TestCase):
    def test_staging_startup_installs_schema_without_backfilling_driver_profiles(self):
        import asyncio
        import ast
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(
            item for item in tree.body
            if isinstance(item, ast.AsyncFunctionDef) and item.name == "on_ready"
        )
        node.decorator_list = []

        ns = {}
        pool = object()

        async def setup_database():
            ns["db_pool"] = pool

        task = SimpleNamespace(is_running=lambda: True, start=lambda: None)
        bot = SimpleNamespace(
            guilds=[SimpleNamespace(id=100)],
            user=SimpleNamespace(id=999),
        )
        backfill = AsyncMock()
        install_unified = AsyncMock()
        install_legacy = AsyncMock()
        ns.update({
            "os": os,
            "GUILD_ID": 100,
            "bot": bot,
            "db_pool": None,
            "unified_schema_ready": False,
            "staging_trucky_probe_ran": False,
            "rotate_bot_status": task,
            "leaderboard_rollover_task": task,
            "setup_database": setup_database,
            "trucky_linking": SimpleNamespace(install_schema=install_legacy),
            "unified_driver_profiles": SimpleNamespace(
                install_schema=install_unified,
                backfill_trucksbook_profiles=backfill,
            ),
            "start_public_integrations": AsyncMock(),
            "print": lambda *args, **kwargs: None,
        })
        exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), ns)
        with patch.dict(os.environ, {
            "AT_V2_ISOLATED_STAGING": "YES",
            "AT_V2_TRUCKY_SCHEMA_PROBE_ON_START": "NO",
        }, clear=False):
            asyncio.run(ns["on_ready"]())

        install_legacy.assert_awaited_once_with(pool)
        install_unified.assert_awaited_once_with(pool)
        backfill.assert_not_awaited()
        ns["start_public_integrations"].assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
