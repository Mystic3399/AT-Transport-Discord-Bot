"""Existing account display is read-only and never promotes ownership evidence."""
import ast
from pathlib import Path
import unittest
from unittest.mock import AsyncMock
import unified_registration_workflow_v2 as registration


class ReadContext:
    def __init__(self, value): self.value = value
    async def __aenter__(self): return self.value
    async def __aexit__(self, *args): pass


class LegacyAccountDisplayTests(unittest.IsolatedAsyncioTestCase):
    async def view(self, legacy, links=()):
        class Connection:
            execute = AsyncMock(side_effect=AssertionError('Display cannot write'))
            def transaction(self, **kwargs):
                assert kwargs == dict(isolation='repeatable_read', readonly=True)
                return ReadContext(self)
            async def fetchrow(self, sql, user):
                if 'FROM driver_links' in sql: return legacy
                return None
            async def fetch(self, sql, user):
                return list(links) if 'FROM unified_provider_links' in sql else []
            async def fetchval(self, sql, *args): return True if 'to_regclass' in sql else False
        conn = Connection()
        class Pool:
            def acquire(self): return ReadContext(conn)
        result = await registration.state(Pool(), 123)
        conn.execute.assert_not_called()
        return result

    async def test_retains_existing_numeric_account_without_steam_or_registration(self):
        view = await self.view(dict(trucksbook_user_id=456, trucksbook_name='Legacy Driver'))
        self.assertEqual(view['accounts']['trucksbook'], dict(status='Linked', account_id='456', display_name='Legacy Driver'))
        self.assertEqual(view['steam_status'], 'Not Linked')
        self.assertFalse(view['ready'])

    async def test_name_only_legacy_record_never_invents_an_id_or_verification(self):
        view = await self.view(dict(trucksbook_user_id=None, trucksbook_name='Legacy Driver'))
        self.assertIsNone(view['accounts']['trucksbook']['account_id'])
        self.assertEqual(view['accounts']['trucksbook']['status'], 'Linked')

    async def test_existing_unified_account_is_not_replaced(self):
        link = dict(provider='trucksbook',provider_account_id='789',provider_display_name='Current Link',verified_at=None)
        view = await self.view(dict(trucksbook_user_id=456,trucksbook_name='Legacy Driver'), [link])
        self.assertEqual(view['accounts']['trucksbook']['account_id'], '789')
        self.assertEqual(view['accounts']['trucksbook']['status'], 'Linked')

    async def test_profile_reads_id_and_preserves_legacy_totals_without_writes(self):
        tree = ast.parse(Path('bot.py').read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'fetch_driver_profile_stats')
        import os
        namespace = {'os':os}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'bot.py','exec'), namespace)
        row = dict(trucksbook_name='Legacy Driver',trucksbook_user_id='456',real_miles=40001,jobs_completed=9,
                   average_job_distance=12,longest_job_distance=22,permanent_achievements=[40000],
                   hall_of_fame_tier=None,immortal_number=None,weekly_wins=0,monthly_wins=0)
        connection = type('Connection',(),{'fetchrow':AsyncMock(return_value=row)})()
        profile = await namespace['fetch_driver_profile_stats'](connection,123)
        self.assertEqual(profile['trucksbook_user_id'],'456')
        self.assertEqual(profile['real_miles'],40001)
        self.assertEqual(profile['permanent_achievements'],(40000,))
        sql = connection.fetchrow.call_args.args[0]
        self.assertIn('COALESCE(links.trucksbook_user_id::text',sql)
        self.assertNotIn('UPDATE ',sql)
        self.assertNotIn('INSERT ',sql)
