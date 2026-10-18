"""Regression tests for the staging-only no-award webhook gate."""
import ast
import asyncio
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")


def load_on_message(namespace):
    tree = ast.parse(SOURCE)
    node = next(
        item for item in tree.body
        if isinstance(item, ast.AsyncFunctionDef) and item.name == "on_message"
    )
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace["on_message"]


class StagingWebhookNoAwardTests(unittest.TestCase):
    def test_authorized_staging_trucksbook_webhook_never_reaches_legacy_writer(self):
        async def run():
            record_job = AsyncMock(return_value={"status": "awarded"})
            link_driver = AsyncMock(return_value=987654321)
            process_commands = AsyncMock()
            handler = load_on_message({
                "GUILD_ID": 100,
                "bot": SimpleNamespace(process_commands=process_commands),
                "record_real_job": record_job,
                "find_driver_link": link_driver,
            })
            message = SimpleNamespace(
                guild=SimpleNamespace(id=100),
                webhook_id=456789,
                channel=SimpleNamespace(id=200),
                embeds=[object()],
            )
            with patch.dict(os.environ, {
                "AT_V2_ISOLATED_STAGING": "YES",
                "TRUCKSBOOK_WEBHOOK_ID": "456789",
            }, clear=False):
                await handler(message)

            record_job.assert_not_awaited()
            link_driver.assert_not_awaited()
            process_commands.assert_not_awaited()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
