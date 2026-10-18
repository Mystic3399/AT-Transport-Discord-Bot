"""Regression tests for the staging-only no-award webhook gate."""
import ast
import asyncio
import contextlib
import io
import os
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")


def load_function(name, namespace):
    tree = ast.parse(SOURCE)
    node = next(
        item for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == name
    )
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace[name]


class Embed:
    def to_dict(self):
        return {
            "title": "Job delivery #27805272",
            "description": "",
            "fields": [{
                "name": "Details",
                "value": "Accepted distance: 125 mil\nStatistics: Real, WoTr",
            }],
        }


class StagingWebhookNoAwardTests(unittest.TestCase):
    def test_authorized_staging_trucksbook_webhook_never_reaches_legacy_writer(self):
        async def run():
            record_job = AsyncMock(return_value={"status": "awarded"})
            link_driver = AsyncMock(return_value=987654321)
            process_commands = AsyncMock()
            namespace = {
                "GUILD_ID": 100,
                "bot": SimpleNamespace(process_commands=process_commands),
                "record_real_job": record_job,
                "find_driver_link": link_driver,
                "re": re,
            }
            for helper in (
                "extract_integer",
                "is_real_statistics",
                "extract_trucksbook_job_from_embed",
            ):
                load_function(helper, namespace)
            handler = load_function("on_message", namespace)
            message = SimpleNamespace(
                guild=SimpleNamespace(id=100),
                webhook_id=456789,
                channel=SimpleNamespace(id=200),
                embeds=[Embed()],
                id=123456789,
            )
            output = io.StringIO()
            with patch.dict(os.environ, {
                "AT_V2_ISOLATED_STAGING": "YES",
                "TRUCKSBOOK_WEBHOOK_ID": "456789",
            }, clear=False), contextlib.redirect_stdout(output):
                with patch("unified_staging_shadow_receipts_v2.record_trucksbook_shadow_receipt",
                           new=AsyncMock(return_value={
                               "status": "quarantined", "duplicate": False,
                               "delivery_count": 1,
                           })) as receipt:
                    namespace["db_pool"] = object()
                    await handler(message)

            self.assertIn("STAGING TRUCKSBOOK QUARANTINED", output.getvalue())
            self.assertIn("job_id=27805272", output.getvalue())
            self.assertIn("classification=real", output.getvalue())
            self.assertIn("receipt=quarantined", output.getvalue())
            self.assertIn("mileage_awarded=no", output.getvalue())
            receipt.assert_awaited_once()
            self.assertEqual(receipt.await_args.kwargs["message_id"], message.id)
            record_job.assert_not_awaited()
            link_driver.assert_not_awaited()
            process_commands.assert_not_awaited()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
