import ast
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unified_driver_profiles


BOT_SOURCE = Path(__file__).resolve().parents[1].joinpath("bot.py").read_text()
TREE = ast.parse(BOT_SOURCE)
OWNER_CHECK = next(
    node for node in TREE.body
    if isinstance(node, ast.FunctionDef) and node.name == "is_authorised_staging_owner"
)
COMMAND = next(
    node for node in TREE.body
    if isinstance(node, ast.AsyncFunctionDef) and node.name == "staging_identity_audit"
)
COMMAND.decorator_list = []
OWNER_ID = 613036707863986236
GUILD_ID = 1561140084739276934


def context(user_id=OWNER_ID, guild_id=GUILD_ID):
    return SimpleNamespace(
        author=SimpleNamespace(id=user_id),
        guild=None if guild_id is None else SimpleNamespace(id=guild_id),
        reply=AsyncMock(),
    )


class StagingIdentityAuditCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.profile_audit = AsyncMock(return_value={
            "steam_verified": True,
            "trucky_claim_status": "pending",
            "trucky_link_verified": False,
            "claim_link_match": None,
        })
        self.profiles = SimpleNamespace(
            get_trucky_identity_link_diagnostics=self.profile_audit
        )
        self.namespace = {
            "os": os,
            "GUILD_ID": GUILD_ID,
            "db_pool": object(),
            "print": lambda *_args, **_kwargs: None,
        }
        exec(compile(ast.Module(
            body=[OWNER_CHECK, COMMAND], type_ignores=[]
        ), "bot.py", "exec"), self.namespace)
        self.run_command = self.namespace["staging_identity_audit"]

    async def test_authorised_staging_owner_gets_redacted_read_only_status(self):
        ctx = context()
        with patch.dict(os.environ, {
            "AT_V2_ISOLATED_STAGING": "YES",
            "AT_V2_STAGING_OWNER_DISCORD_ID": str(OWNER_ID),
        }, clear=True), patch.dict(sys.modules, {
            "unified_driver_profiles": self.profiles,
        }):
            await self.run_command(ctx)
        self.profile_audit.assert_awaited_once_with(self.namespace["db_pool"], OWNER_ID)
        message = ctx.reply.await_args.args[0]
        self.assertIn("Steam verified=yes", message)
        self.assertIn("Trucky claim=pending", message)
        self.assertIn("No registration or mileage data changed", message)

    async def test_rejects_non_owner_and_wrong_guild_before_database_access(self):
        for ctx, env, expected in (
            (context(user_id=42), {
                "AT_V2_ISOLATED_STAGING": "YES",
                "AT_V2_STAGING_OWNER_DISCORD_ID": str(OWNER_ID),
            }, "Only the configured staging owner"),
            (context(guild_id=42), {
                "AT_V2_ISOLATED_STAGING": "YES",
                "AT_V2_STAGING_OWNER_DISCORD_ID": str(OWNER_ID),
            }, "configured A&T staging server"),
        ):
            with self.subTest(expected=expected), patch.dict(os.environ, env, clear=True):
                await self.run_command(ctx)
            self.assertIn(expected, ctx.reply.await_args.args[0])
        self.profile_audit.assert_not_awaited()

    async def test_rejects_missing_or_malformed_staging_owner(self):
        for configured in (None, "abc", f" {OWNER_ID}", "1"):
            ctx = context()
            env = {"AT_V2_ISOLATED_STAGING": "YES"}
            if configured is not None:
                env["AT_V2_STAGING_OWNER_DISCORD_ID"] = configured
            with self.subTest(configured=configured), patch.dict(os.environ, env, clear=True):
                await self.run_command(ctx)
            self.assertIn("configured staging owner", ctx.reply.await_args.args[0])
        self.profile_audit.assert_not_awaited()


class ReadOnlyIdentityQueryTests(unittest.IsolatedAsyncioTestCase):
    async def test_diagnostic_reads_only_link_flags_without_returning_ids(self):
        class Connection:
            query = None
            async def fetchrow(self, query, user_id):
                self.query = query
                self.user_id = user_id
                return {
                    "steam_verified": True,
                    "trucky_claim_status": "pending",
                    "trucky_link_verified": False,
                    "claim_link_match": None,
                }

        class Acquire:
            def __init__(self, connection): self.connection = connection
            async def __aenter__(self): return self.connection
            async def __aexit__(self, *_args): return False

        class Pool:
            def __init__(self): self.connection = Connection()
            def acquire(self): return Acquire(self.connection)

        pool = Pool()
        result = await unified_driver_profiles.get_trucky_identity_link_diagnostics(
            pool, OWNER_ID
        )
        sql = pool.connection.query.upper()
        self.assertTrue(sql.lstrip().startswith("SELECT"))
        self.assertNotRegex(sql, r"\b(INSERT|UPDATE|DELETE|UPSERT)\b")
        self.assertEqual(pool.connection.user_id, OWNER_ID)
        self.assertEqual(result, {
            "steam_verified": True,
            "trucky_claim_status": "pending",
            "trucky_link_verified": False,
            "claim_link_match": None,
        })


if __name__ == "__main__":
    unittest.main()
