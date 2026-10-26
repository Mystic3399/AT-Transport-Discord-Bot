"""Admin overview is private, status-only and read-only."""
import ast
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import unified_registration_workflow_v2 as workflow
from tests.test_registration_workflow_v2 import ENV

CHANNEL = 991234567890127001
ROLE = 991234567890127002


def member(user_id=111, staff=True):
    return SimpleNamespace(id=user_id, bot=False, display_name="Test Member",
                           roles=[ROLE] if staff else [],
                           guild_permissions=SimpleNamespace(administrator=False))


def context():
    author = member()
    channel = SimpleNamespace(id=CHANNEL, parent=None, overwrites={},
        permissions_for=lambda role: SimpleNamespace(view_channel=False))
    guild = SimpleNamespace(id=1, default_role=object(), me=SimpleNamespace(id=999),
                            get_member=lambda user_id: author)
    return SimpleNamespace(author=author, channel=channel, guild=guild, reply=AsyncMock())


class AdminOverviewCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tree = ast.parse(Path("bot.py").read_text())
        functions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.name in ("is_private_verification_admin_channel", "verification_status_overview")]
        for node in functions:
            node.decorator_list = []
        class Embed:
            def __init__(self, **kwargs):
                self.description = kwargs["description"]
                self.fields = []
            def add_field(self, **kwargs): self.fields.append(kwargs)
            def set_footer(self, **kwargs): pass
        self.ns = dict(os=os, GUILD_ID=1, db_pool=object(), VERIFICATION_STAFF_ROLE_IDS=[ROLE],
            member_is_verification_staff=lambda who: ROLE in who.roles,
            discord=SimpleNamespace(Embed=Embed, Colour=SimpleNamespace(blue=lambda: 1),
                utils=SimpleNamespace(escape_markdown=lambda value: value),
                AllowedMentions=SimpleNamespace(none=lambda: "no-mentions")))
        exec(compile(ast.Module(body=functions, type_ignores=[]), "bot.py", "exec"), self.ns)
        self.env = {**ENV, "AT_V2_VERIFICATIONS_ADMIN_CHANNEL_IDS": str(CHANNEL)}
        self.reader = AsyncMock(return_value={"page": 1, "pages": 2, "total": 11,
            "members": [{"discord_user_id": 111, "steam": "Verified",
                         "trucksbook": "Linked", "trucky": "Pending", "registration": "Complete"}]})

    async def test_authorised_private_channel_lists_statuses_without_mentions(self):
        ctx = context()
        with patch.dict(os.environ, self.env, clear=True), patch.object(workflow, "list_registration_statuses", self.reader):
            await self.ns["verification_status_overview"](ctx, 1)
        self.reader.assert_awaited_once_with(self.ns["db_pool"], 1)
        reply = ctx.reply.await_args.kwargs
        self.assertEqual(reply["allowed_mentions"], "no-mentions")
        self.assertIn("Page 1/2", reply["embed"].description)
        self.assertIn("TrucksBook: **Linked**", reply["embed"].fields[0]["value"])
        self.assertNotIn("steamcommunity", reply["embed"].fields[0]["value"])

    async def test_explicit_management_role_and_bot_access_are_allowed(self):
        class Target: pass
        ctx = context()
        staff = Target(); staff.id = ROLE; staff.permissions = SimpleNamespace(administrator=False)
        bot = Target(); bot.id = 999
        ctx.channel.overwrites = {staff: SimpleNamespace(view_channel=True),
                                  bot: SimpleNamespace(view_channel=True)}
        with patch.dict(os.environ, self.env, clear=True):
            self.assertTrue(self.ns["is_private_verification_admin_channel"](ctx))

    async def test_production_registration_does_not_enable_staging_admin_command(self):
        ctx = context()
        with patch.dict(os.environ, {}, clear=True), patch.object(workflow, 'enabled', return_value=True), patch.object(workflow, 'list_registration_statuses', self.reader):
            await self.ns['verification_status_overview'](ctx)
        self.reader.assert_not_awaited()
        self.assertIn('isolated staging', ctx.reply.await_args.args[0])

    async def test_unauthorised_public_wrong_guild_and_invalid_config_never_read_database(self):
        for failure in ("nonstaff", "guild", "public", "channel", "missing", "malformed",
                        "thread", "member_leak", "role_leak", "production"):
            ctx = context(); env = dict(self.env)
            if failure == "nonstaff": ctx.author = member(staff=False)
            elif failure == "guild": ctx.guild.id = 2
            elif failure == "public": ctx.channel.permissions_for = lambda role: SimpleNamespace(view_channel=True)
            elif failure == "channel": ctx.channel.id += 1
            elif failure == "missing": env.pop("AT_V2_VERIFICATIONS_ADMIN_CHANNEL_IDS")
            elif failure == "malformed": env["AT_V2_VERIFICATIONS_ADMIN_CHANNEL_IDS"] = "abc"
            elif failure == "thread": ctx.channel.parent = object()
            elif failure in ("member_leak", "role_leak"):
                class Target: pass
                target = Target(); target.id = 333
                if failure == "member_leak":
                    target.roles = []; target.guild_permissions = SimpleNamespace(administrator=False)
                else:
                    target.permissions = SimpleNamespace(administrator=False)
                ctx.channel.overwrites[target] = SimpleNamespace(view_channel=True)
            elif failure == "production": env["RAILWAY_ENVIRONMENT_ID"] = "production"
            with self.subTest(failure=failure), patch.dict(os.environ, env, clear=True), patch.object(workflow, "list_registration_statuses", self.reader):
                await self.ns["verification_status_overview"](ctx)
        self.reader.assert_not_awaited()


class ReadOnlyOverviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_snapshot_reads_only_and_redacts_provider_ids(self):
        class Connection:
            async def __aenter__(self): return self
            async def __aexit__(self, *_): return False
            def transaction(self, **kwargs):
                self.options = kwargs
                return self
            async def fetchval(self, sql):
                self.assert_select(sql)
                return 1
            def assert_select(self, sql):
                if not sql.lstrip().startswith("SELECT"):
                    raise AssertionError("Only SELECTs are allowed")
            async def fetch(self, sql, *args):
                self.assert_select(sql)
                if "FROM unified_driver_profiles p" in sql:
                    return [dict(discord_user_id=111, profile_status="active",
                                 steam_verified=True, steam_pending=False, registration_status="approved")]
                if "FROM unified_provider_links" in sql:
                    return [dict(discord_user_id=111, provider="trucksbook",
                                 provider_account_id="private-provider-id", verified_at=None)]
                return []
            def acquire(self): return self
        pool = Connection()
        with patch.dict(os.environ, ENV, clear=True):
            result = await workflow.list_registration_statuses(pool)
        self.assertEqual(pool.options, {"isolation": "repeatable_read", "readonly": True})
        self.assertEqual(result["members"][0]["trucksbook"], "Linked")
        self.assertEqual(result["members"][0]["registration"], "Complete")
        self.assertNotIn("private-provider-id", str(result))
        self.assertEqual(result["pages"], 1)

    async def test_invalid_page_and_disabled_mode_fail_before_connection(self):
        class Pool:
            def acquire(self): raise AssertionError("No database access expected")
        for value in (0, -1, True, "1", 1000001):
            with self.subTest(value=value), patch.dict(os.environ, ENV, clear=True), self.assertRaises(ValueError):
                await workflow.list_registration_statuses(Pool(), value)
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            await workflow.list_registration_statuses(Pool())


if __name__ == "__main__":
    unittest.main()

