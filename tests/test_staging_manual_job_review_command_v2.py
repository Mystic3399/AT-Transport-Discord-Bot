"""Owner/guild isolation checks for the no-award manual review command."""
import ast
import os
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
TREE = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
NODE = next(node for node in TREE.body if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "staging_job_review")
NODE.decorator_list = []
OWNER_NODE = next(node for node in TREE.body if isinstance(node, ast.FunctionDef)
                  and node.name == "is_authorised_staging_owner")
OWNER_ID = 613036707863986236
GUILD_ID = 1561140084739276934


def context(*, user_id=OWNER_ID, guild_id=GUILD_ID):
    return SimpleNamespace(author=SimpleNamespace(id=user_id),
                           guild=SimpleNamespace(id=guild_id), reply=AsyncMock())


def command():
    namespace = {"os": os, "GUILD_ID": GUILD_ID, "db_pool": object(),
                 "print": lambda *_args, **_kwargs: None}
    exec(compile(ast.Module(body=[OWNER_NODE, NODE], type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace["staging_job_review"]


class StagingManualJobReviewCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        os.environ.pop("AT_V2_ISOLATED_STAGING", None)

    async def test_rejects_nonstaging_wrong_guild_and_wrong_owner_before_database(self):
        review = SimpleNamespace(open_potential_duplicate=AsyncMock())
        cases = [
            (context(), {"AT_V2_ISOLATED_STAGING": "NO",
                         "AT_V2_STAGING_OWNER_DISCORD_ID": str(OWNER_ID)}),
            (context(guild_id=22), {"AT_V2_ISOLATED_STAGING": "YES",
                                    "AT_V2_STAGING_OWNER_DISCORD_ID": str(OWNER_ID)}),
            (context(user_id=22), {"AT_V2_ISOLATED_STAGING": "YES",
                                   "AT_V2_STAGING_OWNER_DISCORD_ID": str(OWNER_ID)}),
            (context(), {"AT_V2_ISOLATED_STAGING": "YES",
                         "AT_V2_STAGING_OWNER_DISCORD_ID": "bad-id"}),
        ]
        for ctx, env in cases:
            with self.subTest(env=env, guild=ctx.guild.id, user=ctx.author.id), \
                 patch.dict(os.environ, env, clear=False), \
                 patch.dict(sys.modules, {"unified_manual_job_reconciliation_v2": review}):
                await command()(ctx, "open", "27848310", "68093174")
            review.open_potential_duplicate.assert_not_awaited()

    async def test_authorized_open_only_calls_review_ledger(self):
        ctx = context()
        review = SimpleNamespace(open_potential_duplicate=AsyncMock(return_value={
            "case_id": 7, "status": "potential_duplicate", "created": True,
        }))
        with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "YES",
                                     "AT_V2_STAGING_OWNER_DISCORD_ID": str(OWNER_ID)}), \
             patch.dict(sys.modules, {"unified_manual_job_reconciliation_v2": review}):
            await command()(ctx, "open", "27848310", "68093174")
        review.open_potential_duplicate.assert_awaited_once()
        self.assertIn("potential_duplicate", ctx.reply.await_args.args[0])
        self.assertIn("did not award mileage", ctx.reply.await_args.args[0])

    async def test_command_has_no_award_registration_role_or_achievement_path(self):
        names = {node.id for node in ast.walk(NODE) if isinstance(node, ast.Name)}
        forbidden = {"award_staging_miles", "record_normalized_provider_job",
                     "set_driver_mileage", "sync_unified_driver_roles",
                     "register_driver", "grant_company_mods", "add_achievement"}
        self.assertTrue(names.isdisjoint(forbidden))


if __name__ == "__main__":
    unittest.main()
