"""The shadow audit is restricted and read-only in isolated staging."""
import ast
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
TREE = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))


def load_command():
    node = next(item for item in TREE.body
                if isinstance(item, ast.AsyncFunctionDef)
                and item.name == "staging_shadow_audit")
    node.decorator_list = []
    namespace = {
        "os": os,
        "GUILD_ID": 123456789012345678,
        "db_pool": object(),
        "is_authorised_staging_owner": lambda user_id: user_id == 223456789012345678,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace["staging_shadow_audit"]


class Context:
    def __init__(self, *, user_id=223456789012345678, guild_id=123456789012345678):
        self.author = types.SimpleNamespace(id=user_id)
        self.guild = None if guild_id is None else types.SimpleNamespace(id=guild_id)
        self.reply = AsyncMock()


class StagingShadowAuditCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_gets_redacted_read_only_summary_and_job_lookup(self):
        command = load_command()
        pool = command.__globals__["db_pool"]
        report = {
            "receipt_count": 3, "unique_jobs": 2, "replay_count": 1,
            "latest_receipt_at": None, "source_verified": 3,
            "safety_intact": 3, "safety_violations": 0,
            "job": {
                "provider_job_id": "68093174", "receipt_count": 2,
                "replay_count": 1, "first_received_at": None,
                "latest_receipt_at": None, "source_verified": 2,
                "safety_intact": 2, "safety_violations": 0,
            },
        }
        adapter = types.SimpleNamespace(audit_receipts=AsyncMock(return_value=report))
        ctx = Context()
        with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "YES"}), \
             patch.dict(sys.modules, {"unified_staging_shadow_receipts_v2": adapter}):
            await command(ctx, "68093174")
        adapter.audit_receipts.assert_awaited_once_with(pool, "68093174")
        response = ctx.reply.await_args.args[0]
        self.assertIn("Receipts: 3", response)
        self.assertIn("unique jobs: 2", response)
        self.assertIn("replays: 1", response)
        self.assertIn("Job 68093174", response)
        self.assertIn("no writes recorded", response)
        self.assertNotIn("discord_user_id", response)
        self.assertNotIn("webhook token", response.lower())

    async def test_rejects_nonstaging_unauthorised_and_wrong_guild(self):
        command = load_command()
        adapter = types.SimpleNamespace(audit_receipts=AsyncMock())
        with patch.dict(sys.modules, {"unified_staging_shadow_receipts_v2": adapter}):
            ctx = Context()
            with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "NO"}):
                await command(ctx)
            self.assertIn("only in isolated", ctx.reply.await_args.args[0])

            ctx = Context(user_id=323456789012345678)
            with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "YES"}):
                await command(ctx)
            self.assertIn("configured staging owner", ctx.reply.await_args.args[0])

            ctx = Context(guild_id=423456789012345678)
            with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "YES"}):
                await command(ctx)
            self.assertIn("configured A&T staging server", ctx.reply.await_args.args[0])
        adapter.audit_receipts.assert_not_awaited()

    async def test_invalid_job_id_is_rejected_without_lookup(self):
        command = load_command()
        adapter = types.SimpleNamespace(
            audit_receipts=AsyncMock(side_effect=ValueError("Invalid TrucksBook job ID"))
        )
        ctx = Context()
        with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "YES"}), \
             patch.dict(sys.modules, {"unified_staging_shadow_receipts_v2": adapter}):
            await command(ctx, "../private")
        self.assertIn("Invalid TrucksBook job ID", ctx.reply.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
