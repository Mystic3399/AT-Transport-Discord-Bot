"""Connected release regressions; never use real credentials or driver data."""
import ast
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer
import unified_registration_workflow_v2 as workflow
import steam_openid_v2
import steam_callback_v2
from tests.test_registration_workflow_v2 import ENV


class CallbackRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_outage_and_post_commit_retry_are_distinguished(self):
        import asyncio
        token = 'a' * 48
        expected = 'https://example.test/auth/steam/callback?state=' + token
        class Connection:
            async def __aenter__(self): return self
            async def __aexit__(self, *_): pass
            async def fetchrow(self, *_):
                return {'discord_user_id': 111, 'expected_return_to': expected,
                        'valid': True, 'consumed_at': None}
        pool = SimpleNamespace(acquire=Connection)
        app = __import__('trucky_staging_receiver_v2').create_integrations_app(pool)
        client = TestClient(TestServer(app))
        await client.start_server()
        env = {**ENV, 'AT_STEAM_CALLBACK_URL': 'https://example.test/auth/steam/callback'}
        try:
            with patch.dict(os.environ, env, clear=True), patch.object(steam_openid_v2, 'verify_steam_callback', AsyncMock(side_effect=asyncio.TimeoutError)), patch('unified_driver_profiles.bind_verified_steam', new=AsyncMock()) as bind:
                response = await client.get('/auth/steam/callback', params={'state': token, 'openid.return_to': expected})
                self.assertEqual(response.status, 503)
                bind.assert_not_awaited()
            with patch.dict(os.environ, env, clear=True), patch.object(steam_openid_v2, 'verify_steam_callback', AsyncMock(return_value='76561198888888990')), patch('unified_driver_profiles.bind_verified_steam', new=AsyncMock()) as bind, patch.object(workflow, 'complete_if_ready', AsyncMock(side_effect=RuntimeError('temporary outage'))):
                response = await client.get('/auth/steam/callback', params={'state': token, 'openid.return_to': expected})
                self.assertEqual(response.status, 200)
                self.assertIn('Run /register', await response.text())
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                bind.assert_awaited_once()
        finally:
            await client.close()


class RegistrationReleaseSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_signed_identity_callback_only_refreshes_registration(self):
        tree = ast.parse(Path('bot.py').read_text())
        parent = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                      and n.name == 'start_public_integrations')
        callback = next(n for n in parent.body if isinstance(n, ast.Try))
        callback = next(n for n in callback.body if isinstance(n, ast.AsyncFunctionDef)
                        and n.name == 'on_trucky_linked')
        refresh, roles = AsyncMock(), AsyncMock()
        ns = {'refresh_unified_registration': refresh,
              'sync_unified_driver_roles': roles,
              'bot': SimpleNamespace(get_guild=lambda _: self.fail('No role guild lookup'))}
        exec(compile(ast.Module(body=[callback], type_ignores=[]), 'bot.py', 'exec'), ns)
        await ns['on_trucky_linked'](111)
        refresh.assert_awaited_once_with(111)
        roles.assert_not_awaited()

    async def test_unapproved_production_callback_rejects_before_reading_database(self):
        pool = SimpleNamespace(acquire=AsyncMock())
        app = __import__('trucky_staging_receiver_v2').create_integrations_app(pool)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            with patch.dict(os.environ, {'RAILWAY_ENVIRONMENT_ID': workflow.PRODUCTION_ENVIRONMENT}, clear=True):
                response = await client.get('/auth/steam/callback?state=' + 'a' * 48)
            self.assertEqual(response.status, 503)
            pool.acquire.assert_not_awaited()
        finally:
            await client.close()

    async def test_state_never_labels_suspended_cancelled_or_unapproved_complete(self):
        class Connection:
            def __init__(self, profile, session):
                self.profile, self.session = profile, session
            async def __aenter__(self): return self
            async def __aexit__(self, *_): pass
            def transaction(self, **kwargs):
                self_test.assertEqual(kwargs, {'isolation': 'repeatable_read', 'readonly': True})
                return self
            async def fetchrow(self, query, *_):
                if 'FROM unified_driver_profiles' in query: return {'status': self.profile}
                if 'FROM unified_registration_sessions' in query: return {'status': self.session}
                return {'steam_id64': '76561198888888990', 'verified_at': object()}
            async def fetch(self, query, *_):
                if 'FROM unified_provider_links' in query:
                    return [{'provider': 'trucky', 'verified_at': None,
                             'provider_account_id': '9900', 'provider_display_name': 'Test'}]
                return []
            async def fetchval(self, *_): return False
        self_test = self
        for profile, session, expected in [('active', 'approved', True),
                ('suspended', 'approved', False), ('active', 'cancelled', False),
                ('active', 'pending', False), ('pending', 'approved', False)]:
            conn = Connection(profile, session)
            view = await workflow.state(SimpleNamespace(acquire=lambda: conn), 111)
            self.assertEqual(view['ready'], expected, (profile, session))
            self.assertEqual(view['accounts']['trucky']['status'], 'Linked')


class CallbackOriginTests(unittest.TestCase):
    def test_realm_rejects_query_fragment_credentials_and_wrong_path(self):
        callback = 'https://example.test/auth/steam/callback?state=opaque'
        for realm in ('https://example.test/?x=1', 'https://example.test/#fragment',
                      'https://user:password@example.test', 'https://example.test/other'):
            with self.subTest(realm=realm), self.assertRaises(ValueError):
                steam_openid_v2.make_login_url(callback, realm)

