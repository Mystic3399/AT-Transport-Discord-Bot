"""Isolated behavioural checks for the owner-only staging role command."""
import ast
import os
import pathlib
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
COMMAND_NODE = next(
    node for node in TREE.body
    if isinstance(node, ast.AsyncFunctionDef) and node.name == "staging_role_sync"
)
COMMAND_NODE.decorator_list = []
OWNER_CHECK_NODE = next(
    node for node in TREE.body
    if isinstance(node, ast.FunctionDef) and node.name == "is_authorised_staging_owner"
)


class FakeForbidden(Exception):
    pass


class FakeHTTPException(Exception):
    pass


NAMESPACE = {
    "os": os,
    "GUILD_ID": 1561140084739276934,
    "discord": SimpleNamespace(Forbidden=FakeForbidden, HTTPException=FakeHTTPException),
    "sync_unified_driver_roles": AsyncMock(),
    "print": lambda *_args, **_kwargs: None,
}
exec(compile(ast.Module(body=[OWNER_CHECK_NODE, COMMAND_NODE], type_ignores=[]), "bot.py", "exec"), NAMESPACE)
COMMAND = NAMESPACE["staging_role_sync"]
OWNER_ID = 613036707863986236


def make_context(user_id=OWNER_ID, guild_id=1561140084739276934):
    return SimpleNamespace(
        author=SimpleNamespace(id=user_id),
        guild=None if guild_id is None else SimpleNamespace(id=guild_id),
        reply=AsyncMock(),
    )


class StagingRoleSyncCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        NAMESPACE["sync_unified_driver_roles"].reset_mock(
            return_value=True, side_effect=True
        )

    async def test_disabled_outside_isolated_staging(self):
        ctx = make_context()
        with unittest.mock.patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "NO"}):
            await COMMAND(ctx)
        NAMESPACE["sync_unified_driver_roles"].assert_not_awaited()
        self.assertIn("only in isolated", ctx.reply.await_args.args[0])

    async def test_rejects_missing_or_malformed_owner_configuration(self):
        for configured in (None, "abc", " 613036707863986236", "1"):
            with self.subTest(configured=configured):
                ctx = make_context()
                env = {"AT_V2_ISOLATED_STAGING": "YES"}
                if configured is not None:
                    env["AT_V2_STAGING_OWNER_DISCORD_ID"] = configured
                with unittest.mock.patch.dict(os.environ, env, clear=True):
                    await COMMAND(ctx)
                NAMESPACE["sync_unified_driver_roles"].assert_not_awaited()
                self.assertIn("configured staging owner", ctx.reply.await_args.args[0])

    async def test_rejects_wrong_guild(self):
        ctx = make_context(guild_id=999)
        with unittest.mock.patch.dict(os.environ, {
            "AT_V2_ISOLATED_STAGING": "YES",
            "AT_V2_STAGING_OWNER_DISCORD_ID": str(OWNER_ID),
        }, clear=True):
            await COMMAND(ctx)
        NAMESPACE["sync_unified_driver_roles"].assert_not_awaited()
        self.assertIn("configured A&T staging server", ctx.reply.await_args.args[0])

    async def test_rejects_non_owner_before_role_sync(self):
        ctx = make_context(user_id=42)
        with unittest.mock.patch.dict(os.environ, {
            "AT_V2_ISOLATED_STAGING": "YES",
            "AT_V2_STAGING_OWNER_DISCORD_ID": str(OWNER_ID),
        }, clear=True):
            await COMMAND(ctx)
        NAMESPACE["sync_unified_driver_roles"].assert_not_awaited()
        self.assertIn("Only the configured", ctx.reply.await_args.args[0])

    async def test_invokes_existing_role_sync_for_authorised_owner(self):
        ctx = make_context()
        NAMESPACE["sync_unified_driver_roles"].return_value = {
            "status": "updated", "role_name": "A&T New Driver"
        }
        with unittest.mock.patch.dict(os.environ, {
            "AT_V2_ISOLATED_STAGING": "YES",
            "AT_V2_STAGING_OWNER_DISCORD_ID": str(OWNER_ID),
        }, clear=True):
            await COMMAND(ctx)
        NAMESPACE["sync_unified_driver_roles"].assert_awaited_once_with(
            ctx.guild, ctx.author.id
        )
        self.assertIn("assigned/updated", ctx.reply.await_args.args[0])

    async def test_reports_unchanged_and_missing_roles(self):
        for result, expected in [
            ({"status": "correct", "role_name": "Road Runner"}, "already correct"),
            ({"status": "role_missing", "role_name": "Road Runner"}, "is missing"),
        ]:
            with self.subTest(status=result["status"]):
                ctx = make_context()
                NAMESPACE["sync_unified_driver_roles"].return_value = result
                with unittest.mock.patch.dict(os.environ, {
                    "AT_V2_ISOLATED_STAGING": "YES",
                    "AT_V2_STAGING_OWNER_DISCORD_ID": str(OWNER_ID),
                }, clear=True):
                    await COMMAND(ctx)
                self.assertIn(expected, ctx.reply.await_args.args[0])

    async def test_reports_discord_permission_error(self):
        ctx = make_context()
        NAMESPACE["sync_unified_driver_roles"].side_effect = FakeForbidden()
        with unittest.mock.patch.dict(os.environ, {
            "AT_V2_ISOLATED_STAGING": "YES",
            "AT_V2_STAGING_OWNER_DISCORD_ID": str(OWNER_ID),
        }, clear=True):
            await COMMAND(ctx)
        self.assertIn("Manage Roles", ctx.reply.await_args.args[0])

    def test_command_has_no_database_or_mileage_write_path(self):
        names = {node.id for node in ast.walk(COMMAND_NODE) if isinstance(node, ast.Name)}
        self.assertNotIn("db_pool", names)
        self.assertNotIn("record_normalized_provider_job", names)
        self.assertNotIn("submit_provider_claim", names)


if __name__ == "__main__":
    unittest.main()
