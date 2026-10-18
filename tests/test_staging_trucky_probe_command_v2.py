"""Behavioral checks for the staging-only, redacted Trucky schema probe."""
import ast
import os
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
NODE = next(
    node for node in TREE.body
    if isinstance(node, ast.AsyncFunctionDef)
    and node.name == "staging_trucky_jobs_probe"
)
NODE.decorator_list = []
OWNER_CHECK_NODE = next(
    node for node in TREE.body
    if isinstance(node, ast.FunctionDef) and node.name == "is_authorised_staging_owner"
)


class FakeUnavailable(Exception):
    pass


NAMESPACE = {
    "os": os,
    "json": __import__("json"),
    "GUILD_ID": 1561140084739276934,
    "print": lambda *_args, **_kwargs: None,
}
exec(compile(ast.Module(body=[OWNER_CHECK_NODE, NODE], type_ignores=[]), "bot.py", "exec"), NAMESPACE)
COMMAND = NAMESPACE["staging_trucky_jobs_probe"]
OWNER_ID = 613036707863986236


def context(user_id=OWNER_ID):
    return SimpleNamespace(
        author=SimpleNamespace(id=user_id),
        guild=SimpleNamespace(id=1561140084739276934),
        reply=AsyncMock(),
    )


class StagingTruckyProbeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        os.environ.pop("AT_V2_ISOLATED_STAGING", None)

    async def test_refuses_outside_staging(self):
        ctx = context()
        with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "NO"}):
            await COMMAND(ctx)
        self.assertIn("only in isolated", ctx.reply.await_args.args[0])

    async def test_refuses_non_owner_without_api_call(self):
        ctx = context(user_id=42)
        api = SimpleNamespace(fetch_trucky_jobs_page=AsyncMock())
        with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "YES", "AT_V2_STAGING_OWNER_DISCORD_ID": str(OWNER_ID)}), patch.dict(sys.modules, {"trucky_jobs_api_v2": api}):
            await COMMAND(ctx)
        api.fetch_trucky_jobs_page.assert_not_awaited()
        self.assertIn("Only the configured", ctx.reply.await_args.args[0])

    async def test_returns_types_without_job_or_driver_values(self):
        ctx = context()
        api = SimpleNamespace(
            fetch_trucky_jobs_page=AsyncMock(return_value={"data": [{"id": 123456, "driver": {"id": 98765, "name": "PRIVATE"}}]}),
            inspect_jobs_response_shape=Mock(return_value={"outer_keys": ["data"], "record_count": 1, "first_record_types": {"id": "int"}, "first_record_nested_types": {"driver": {"id": "int", "name": "str"}}, "verified_delivery": False}),
            TruckyJobLookupUnavailable=FakeUnavailable,
        )
        with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "YES", "AT_V2_STAGING_OWNER_DISCORD_ID": str(OWNER_ID)}), patch.dict(sys.modules, {"trucky_jobs_api_v2": api}):
            await COMMAND(ctx)
        api.fetch_trucky_jobs_page.assert_awaited_once_with(page=1)
        response = ctx.reply.await_args.args[0]
        self.assertIn('"id": "int"', response)
        self.assertNotIn("PRIVATE", response)
        self.assertNotIn("123456", response)
        self.assertNotIn("98765", response)

    def test_probe_has_no_database_or_award_write_path(self):
        names = {node.id for node in ast.walk(NODE) if isinstance(node, ast.Name)}
        self.assertNotIn("db_pool", names)
        self.assertNotIn("award_staging_miles", names)
        self.assertNotIn("record_normalized_provider_job", names)


if __name__ == "__main__":
    unittest.main()
