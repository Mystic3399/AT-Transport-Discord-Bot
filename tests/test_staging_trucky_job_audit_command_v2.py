"""Owner/guild/safe-mode behavior for the exact-job audit command."""
import ast
import json
import os
import pathlib
import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
TREE = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
NODE = next(node for node in TREE.body if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "staging_trucky_job_audit")
NODE.decorator_list = []
OWNER_NODE = next(node for node in TREE.body if isinstance(node, ast.FunctionDef)
                  and node.name == "is_authorised_staging_owner")
OWNER_ID = 613036707863986236


def context(*, user_id=OWNER_ID, guild_id=1561140084739276934):
    return SimpleNamespace(author=SimpleNamespace(id=user_id),
                           guild=SimpleNamespace(id=guild_id), reply=AsyncMock())


def command():
    namespace = {
        "os": os, "json": json, "GUILD_ID": 1561140084739276934,
        "db_pool": object(), "print": lambda *_args, **_kwargs: None,
    }
    exec(compile(ast.Module(body=[OWNER_NODE, NODE], type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace["staging_trucky_job_audit"]


class StagingExactJobAuditCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        os.environ.pop("AT_V2_ISOLATED_STAGING", None)

    async def test_refuses_when_not_isolated_staging(self):
        ctx = context()
        with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "NO"}):
            await command()(ctx, "27848310")
        self.assertIn("only in isolated", ctx.reply.await_args.args[0])

    async def test_refuses_wrong_guild_or_user_before_api_access(self):
        for ctx in (context(guild_id=2), context(user_id=2)):
            api = SimpleNamespace(fetch_trucky_jobs_page=AsyncMock())
            with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "YES",
                                         "AT_V2_STAGING_OWNER_DISCORD_ID": str(OWNER_ID)}), \
                 patch.dict(sys.modules, {"trucky_jobs_api_v2": api}):
                await command()(ctx, "27848310")
            api.fetch_trucky_jobs_page.assert_not_awaited()

    async def test_returns_redacted_api_facts_and_receipt_without_any_write(self):
        ctx = context()
        api = SimpleNamespace(
            TruckyJobLookupUnavailable=type("TruckyJobLookupUnavailable", (Exception,), {}),
            fetch_trucky_jobs_page=AsyncMock(return_value={"data": [{
                "id": 27848310, "status": "completed", "distance_unit": "mi",
                "driven_distance": 176, "mileage_type": "real",
                "driver": {"id": 999, "name": "PRIVATE"},
            }]}),
        )
        # Use the actual pure helper implementation while keeping API transport mocked.
        import trucky_jobs_api_v2
        api.find_job_record = trucky_jobs_api_v2.find_job_record
        api.safe_job_record_report = trucky_jobs_api_v2.safe_job_record_report
        api.inspect_job_identity_fields = trucky_jobs_api_v2.inspect_job_identity_fields
        profile = SimpleNamespace(
            list_trucky_receipt_audit=AsyncMock(return_value=[{
                "event_type": "job_completed",
                "received_at": datetime(2026, 10, 18, 20, 17, tzinfo=timezone.utc),
                "review_status": "needs_api_verification",
            }]),
            verify_trucky_account_against_verified_links=AsyncMock(return_value={
                "status": "verified_trucky_account_link_matches_steam",
            }),
        )
        membership = SimpleNamespace(verify_member=AsyncMock(return_value="redacted"),
                                     MembershipUnavailable=type("MembershipUnavailable", (Exception,), {}))
        with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "YES",
                                     "AT_V2_STAGING_OWNER_DISCORD_ID": str(OWNER_ID)}), \
             patch.dict(sys.modules, {"trucky_jobs_api_v2": api,
                                      "unified_driver_profiles": profile,
                                      "trucky_membership_adapter": membership}):
            await command()(ctx, "27848310")
        response = ctx.reply.await_args.args[0]
        self.assertIn('"requested_job_found": true', response)
        self.assertIn('"identity_verification": "verified_trucky_account_link_matches_steam"', response)
        self.assertIn('"signed_event_job_id_matches_api_job": true', response)
        self.assertIn('"mileage_awarded": false', response)
        self.assertIn('"registration_changed": false', response)
        self.assertIn('"roles_changed": false', response)
        self.assertLess(len(response), 1800)
        self.assertNotIn("PRIVATE", response)
        self.assertNotIn('"driver":{"id":999', response)
        profile.list_trucky_receipt_audit.assert_awaited_once()
        profile.verify_trucky_account_against_verified_links.assert_awaited_once()
        self.assertEqual(
            profile.verify_trucky_account_against_verified_links.await_args.args[1],
            "999",
        )
        names = {n.id for n in ast.walk(NODE) if isinstance(n, ast.Name)}
        forbidden = {"award_staging_miles", "record_normalized_provider_job",
                     "set_driver_mileage", "sync_unified_driver_roles",
                     "register_driver", "grant_company_mods"}
        self.assertTrue(names.isdisjoint(forbidden))


if __name__ == "__main__":
    unittest.main()
