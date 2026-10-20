"""A witnessed provider mapping may be compared, never approved by a digest."""
import hashlib
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import os
import sys

import unified_driver_profiles as profiles
from tests.test_staging_identity_audit_v2 import StagingIdentityAuditCommandTests, context, OWNER_ID

STEAM = '76561198000000072'
DIGEST = hashlib.sha256(STEAM.encode()).hexdigest()

class Connection:
    def __init__(self):
        self.row = dict(steam_id64=STEAM, steam_verified_at=datetime.now(timezone.utc),
                       claimed_account='123456', claim_status='pending', linked_account=None,
                       conflicting_owner=False)
        self.calls = []
    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        return self.row
    async def __aenter__(self): return self
    async def __aexit__(self, *_): return False
    def acquire(self): return self

class ObservedIdentityAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_matching_identity_is_redacted_read_only_and_not_approved(self):
        pool = Connection()
        result = await profiles.audit_trucksbook_observed_identity(pool, 111, '123456', DIGEST)
        self.assertTrue(result['observed_steam_match'])
        self.assertTrue(result['claim_matches'])
        self.assertFalse(result['ownership_approved'])
        self.assertNotIn(STEAM, str(result))
        sql, args = pool.calls[0]
        self.assertTrue(sql.lstrip().startswith('SELECT'))
        self.assertNotRegex(sql.upper(), r'\b(INSERT|UPDATE|DELETE)\b')
        self.assertEqual(args, (111, '123456'))

    async def test_missing_unverified_mismatched_and_conflicting_evidence(self):
        for changes in ({'steam_id64': None, 'steam_verified_at': None},
                        {'steam_verified_at': None}, {'steam_id64': '76561198000000073'},
                        {'claimed_account': '999999'}, {'conflicting_owner': True}):
            with self.subTest(changes=changes):
                pool = Connection(); pool.row.update(changes)
                result = await profiles.audit_trucksbook_observed_identity(pool, 111, '123456', DIGEST)
                self.assertFalse(result['ownership_approved'])
                if 'steam_id64' in changes or 'steam_verified_at' in changes:
                    self.assertFalse(result['observed_steam_match'])
                if 'claimed_account' in changes: self.assertFalse(result['claim_matches'])
                if 'conflicting_owner' in changes: self.assertTrue(result['conflicting_owner'])

    async def test_invalid_inputs_fail_before_database_access(self):
        for account, digest in ((None, DIGEST), ('１２３', DIGEST), ('123', None),
                                ('123', STEAM), ('123', 'A'*64), ('1'*31, DIGEST)):
            pool = Connection()
            with self.subTest(account=account), self.assertRaises(ValueError):
                await profiles.audit_trucksbook_observed_identity(pool, 111, account, digest)
            self.assertEqual(pool.calls, [])

class ObservedIdentityCommandTests(StagingIdentityAuditCommandTests):
    async def test_optional_comparison_uses_owner_only_and_redacts_inputs(self):
        audit = AsyncMock(return_value=dict(steam_verified=True, observed_steam_match=True,
                    claim_matches=True, existing_link_matches=False, conflicting_owner=False))
        self.profiles.audit_trucksbook_observed_identity = audit
        with patch.dict(os.environ, {'AT_V2_ISOLATED_STAGING': 'YES',
                'AT_V2_STAGING_OWNER_DISCORD_ID': str(OWNER_ID)}, clear=True), \
                patch.dict(sys.modules, {'unified_driver_profiles': self.profiles}):
            ctx = context()
            await self.run_command(ctx, '123456', DIGEST)
            audit.assert_awaited_once_with(self.namespace['db_pool'], OWNER_ID, '123456', DIGEST)
            reply = ctx.reply.await_args.args[0]
            self.assertIn('observed_steam_match=yes', reply)
            self.assertIn('Ownership not approved', reply)
            self.assertNotIn(DIGEST, reply)
            self.assertNotIn('123456', reply)
            audit.reset_mock()
            await self.run_command(context(user_id=42), '123456', DIGEST)
            audit.assert_not_awaited()
