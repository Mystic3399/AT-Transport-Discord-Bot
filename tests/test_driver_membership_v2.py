import unittest
from unittest.mock import AsyncMock, Mock
import discord
from driver_membership_v2 import company_status, inspect_driver


class StatusTests(unittest.TestCase):
    def test_membership_is_not_discord_presence(self):
        self.assertEqual(company_status('not_member', 'not_member'), 'Former company driver')
        self.assertEqual(company_status('member', 'not_member'), 'Active company driver')
        self.assertEqual(company_status('unknown', 'not_member'), 'Company status unconfirmed')
        self.assertEqual(company_status('unknown', 'unknown'), 'Company status unconfirmed')


class InspectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_stable_ids_not_names_and_no_writes(self):
        guild = Mock(); guild.get_member.return_value = object()
        result = await inspect_driver({'discord_user_id': 1, 'trucksbook_user_id': 2,
            'trucky_account_id': '3'}, guild, {'3'}, AsyncMock(return_value={
                'success': True, 'belongs_to_at': True}))
        self.assertEqual(result['trucky'], 'member')
        self.assertEqual(result['company'], 'Active company driver')
        self.assertEqual(result['discord'], 'In server')

    async def test_missing_profile_is_not_departure(self):
        guild = Mock(); guild.get_member.return_value = object()
        result = await inspect_driver({'discord_user_id': 1, 'trucksbook_user_id': 2,
            'trucky_account_id': '3'}, guild, set(), AsyncMock(return_value={
                'success': True, 'belongs_to_at': False}))
        self.assertEqual(result['company'], 'Company status unconfirmed')

    async def test_provider_outage_does_not_mark_former(self):
        guild = Mock(); guild.get_member.return_value = object()
        result = await inspect_driver({'discord_user_id': 1, 'trucky_account_id': '3'},
            guild, None, AsyncMock(side_effect=TimeoutError()))
        self.assertEqual(result['trucky'], 'unknown')
        self.assertEqual(result['company'], 'Company status unconfirmed')

    async def test_discord_cache_miss_checked_via_api(self):
        guild = Mock(); guild.get_member.return_value = None
        guild.fetch_member = AsyncMock(return_value=object())
        result = await inspect_driver({'discord_user_id': 1}, guild, set(), AsyncMock())
        guild.fetch_member.assert_awaited_once_with(1)
        self.assertEqual(result['discord'], 'In server')


class CommandSafetyTests(unittest.TestCase):
    def test_admin_guild_checks_and_readonly_sql(self):
        import ast
        from pathlib import Path
        source = Path('bot.py').read_text()
        tree = ast.parse(source)
        for name in ('check_drivers_v2', 'check_driver_v2'):
            node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
            decorators = [ast.unparse(d) for d in node.decorator_list]
            self.assertTrue(any('has_permissions(administrator=True)' in d for d in decorators))
            self.assertTrue(any('guild_only' in d for d in decorators))
            self.assertIn('ctx.guild.id != GUILD_ID', ast.unparse(node))
        node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'refresh_driver_memberships_v2')
        self.assertNotIn('.execute(', ast.unparse(node))
        self.assertNotIn('real_miles =', ast.unparse(node))
