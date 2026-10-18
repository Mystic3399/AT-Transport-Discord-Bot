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
            self.assertIn("STAGING WEBHOOK OBSERVED source=trucksbook_configured source_match=yes guild_match=yes", output.getvalue())
            self.assertIn("channel_match=not_configured", output.getvalue())
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

    def test_wrong_source_or_guild_fails_closed_with_redacted_diagnostic(self):
        async def run(message, extra_env=None):
            receipt = AsyncMock()
            process_commands = AsyncMock()
            namespace = {"GUILD_ID": 100, "bot": SimpleNamespace(process_commands=process_commands), "re": re}
            for helper in ("extract_integer", "is_real_statistics", "extract_trucksbook_job_from_embed"):
                load_function(helper, namespace)
            handler = load_function("on_message", namespace)
            namespace["db_pool"] = object()
            env = {"AT_V2_ISOLATED_STAGING": "YES", "TRUCKSBOOK_WEBHOOK_ID": "456789"}
            if extra_env:
                env.update(extra_env)
            output = io.StringIO()
            with patch.dict(os.environ, env, clear=False), contextlib.redirect_stdout(output):
                with patch("unified_staging_shadow_receipts_v2.record_trucksbook_shadow_receipt", new=receipt):
                    await handler(message)
            return output.getvalue(), receipt, process_commands

        async def scenario():
            wrong_source = SimpleNamespace(guild=SimpleNamespace(id=100), webhook_id=999999,
                                           channel=SimpleNamespace(id=200), embeds=[Embed()], id=123)
            out, receipt, commands = await run(wrong_source)
            self.assertIn("source_match=no", out)
            self.assertIn("route=rejected", out)
            self.assertNotIn("999999", out)
            receipt.assert_not_awaited()
            commands.assert_not_awaited()

            wrong_guild = SimpleNamespace(guild=SimpleNamespace(id=101), webhook_id=456789,
                                          channel=SimpleNamespace(id=200), embeds=[Embed()], id=124)
            out, receipt, commands = await run(wrong_guild)
            self.assertIn("guild_match=no", out)
            receipt.assert_not_awaited()
            commands.assert_not_awaited()

            wrong_channel = SimpleNamespace(guild=SimpleNamespace(id=100), webhook_id=456789,
                                             channel=SimpleNamespace(id=201), embeds=[Embed()], id=125)
            out, receipt, _ = await run(wrong_channel, {"TRUCKSBOOK_STAGING_CHANNEL_ID": "200"})
            self.assertIn("channel_match=mismatch", out)
            receipt.assert_not_awaited()

        asyncio.run(scenario())

    def test_malformed_event_and_receipt_failure_never_fall_through(self):
        async def run():
            process_commands = AsyncMock()
            legacy_writer = AsyncMock()
            namespace = {"GUILD_ID": 100, "bot": SimpleNamespace(process_commands=process_commands),
                         "record_real_job": legacy_writer, "re": re}
            for helper in ("extract_integer", "is_real_statistics", "extract_trucksbook_job_from_embed"):
                load_function(helper, namespace)
            handler = load_function("on_message", namespace)
            namespace["db_pool"] = object()
            malformed = SimpleNamespace(to_dict=lambda: {"title": "no provider job"})
            message = SimpleNamespace(guild=SimpleNamespace(id=100), webhook_id=456789,
                                      channel=SimpleNamespace(id=200), embeds=[malformed], id=126)
            with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "YES",
                                         "TRUCKSBOOK_WEBHOOK_ID": "456789"}, clear=False):
                with patch("unified_staging_shadow_receipts_v2.record_trucksbook_shadow_receipt",
                           new=AsyncMock(return_value={"status": "malformed", "duplicate": False,
                                                      "delivery_count": 1})) as receipt:
                    await handler(message)
                    self.assertIsNone(receipt.await_args.kwargs["parsed_job"])
                with patch("unified_staging_shadow_receipts_v2.record_trucksbook_shadow_receipt",
                           new=AsyncMock(side_effect=RuntimeError("storage failure"))):
                    await handler(message)
            legacy_writer.assert_not_awaited()
            process_commands.assert_not_awaited()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
