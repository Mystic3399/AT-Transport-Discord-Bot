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

class ProfileReviewFailClosedTests(unittest.IsolatedAsyncioTestCase):
    async def test_requires_isolated_staging_independent_reviewer_and_complete_evidence(self):
        class NoDB:
            def acquire(self): raise AssertionError('Must reject before database access')
        for env, reviewer, witnessed, account, digest in (
            ({}, 222, True, '123456', DIGEST),
            ({'AT_V2_ISOLATED_STAGING':'YES'}, 111, True, '123456', DIGEST),
            ({'AT_V2_ISOLATED_STAGING':'YES'}, 222, False, '123456', DIGEST),
            ({'AT_V2_ISOLATED_STAGING':'YES'}, 222, True, 'bad', DIGEST),
            ({'AT_V2_ISOLATED_STAGING':'YES'}, 222, True, '123456', 'bad'),
        ):
            with self.subTest(reviewer=reviewer, env=env), patch.dict(os.environ, env, clear=True):
                with self.assertRaises(ValueError):
                    await profiles.review_trucksbook_authenticated_profile(
                        NoDB(), 111, reviewer, STEAM, account, digest,
                        witnessed_profile_mapping=witnessed)

    def test_staff_review_category_cannot_authorise_automatic_mileage(self):
        from unified_mileage_engine_control_v2 import verified_job_evidence_reason
        job = dict(provider='trucksbook', provider_job_id='68093174',
                   provider_account_id='123456', discord_user_id=111,
                   provider_authenticated=True, completed=True, job_status='completed',
                   ownership_verified=True, steam_identity_verified=True,
                   ownership_evidence_kind='trucksbook_staff_reviewed_profile_steam_mapping')
        self.assertEqual(verified_job_evidence_reason(job), 'ownership_evidence_kind_unverified')

class ProfileReviewCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_staff_review_routes_only_in_staging_without_role_or_mileage_calls(self):
        import ast
        from pathlib import Path
        command = next(n for n in ast.parse(Path('bot.py').read_text()).body
                       if isinstance(n, ast.AsyncFunctionDef) and n.name == 'approve_unified_provider')
        command.decorator_list = []
        for staging, staff, reviewer, marker, accepted in (
            ('YES', True, 222, 'witnessed-profile-steam-member', True),
            ('NO', True, 222, 'witnessed-profile-steam-member', False),
            ('YES', False, 222, 'witnessed-profile-steam-member', False),
            ('YES', True, 111, 'witnessed-profile-steam-member', False),
            ('YES', True, 222, 'confirm-member', False),
        ):
            review = AsyncMock()
            helper = SimpleNamespace(
                get_pending_claim=AsyncMock(return_value={'status':'approved'}),
                review_trucksbook_authenticated_profile=review)
            namespace = dict(os=os, GUILD_ID=1, db_pool=object(), unified_driver_profiles=helper,
                    member_is_verification_staff=lambda author: staff,
                    get_verification_by_channel=AsyncMock(return_value={'discord_user_id':111}))
            exec(compile(ast.Module(body=[command],type_ignores=[]),'bot.py','exec'), namespace)
            ctx = SimpleNamespace(guild=SimpleNamespace(id=1), channel=SimpleNamespace(id=2),
                                  author=SimpleNamespace(id=reviewer), reply=AsyncMock())
            with self.subTest(staging=staging, reviewer=reviewer, marker=marker), \
                    patch.dict(os.environ, {'AT_V2_ISOLATED_STAGING':staging}, clear=True):
                await namespace['approve_unified_provider'](ctx,'trucksbook',STEAM,'123456',DIGEST,marker)
                if accepted:
                    review.assert_awaited_once_with(namespace['db_pool'],111,reviewer,STEAM,'123456',DIGEST,witnessed_profile_mapping=True)
                    self.assertIn('No mileage, roles, achievements',ctx.reply.await_args.args[0])
                else:
                    review.assert_not_awaited()
