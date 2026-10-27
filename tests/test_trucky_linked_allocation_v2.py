import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import os
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from trucky_linked_allocation_v2 import activation_allowed, vault_candidate, _settle_candidate, allocate_saved_linked_history
from unified_distance_units import KM_TO_MILES


def observation(job='701', account='456', km='1', kind='real_miles', **changes):
    evidence=dict(game_id=1,status='completed',stats_type=kind,driven_distance_km=km,
                  distance_unit='km',completed_at='2026-10-01T12:00:00Z')
    evidence.update(changes)
    return dict(provider='trucky',provider_job_id=job,provider_account_id=account,
                source_kind='authenticated_api',evidence=evidence)


class LinkedAllocationTests(unittest.IsolatedAsyncioTestCase):
    def config(self):
        return dict(AT_V2_LINKED_TRUCKY_MILEAGE_ENABLED='YES',AT_V2_PENDING_HISTORY_ENABLED='YES',
            AT_V2_TRUCKY_API_INGEST_ENABLED='YES',RAILWAY_PROJECT_ID='00313eef-3512-45ec-a4fa-fdcb815235a2',
            RAILWAY_ENVIRONMENT_ID='72d029c5-98b4-4440-8492-8db12620d9da',
            RAILWAY_SERVICE_ID='73f8817c-36be-423a-b68b-b457232cfcfd',
            AT_V2_LINKED_MILEAGE_OWNER_APPROVAL_REF='owner-approved-accepted-mapping',
            AT_V2_LINKED_MILEAGE_DATABASE_SHA256='d'*64, RAILWAY_GIT_COMMIT_SHA='c'*40,
            AT_V2_REGISTRATION_APPROVED_COMMIT='c'*40)

    def test_boundary_requires_runtime_approval_and_database_identity(self):
        config=self.config()
        self.assertTrue(activation_allowed(config))
        for key in config:
            with self.subTest(key=key):
                self.assertFalse(activation_allowed({**config,key:''}))
        for key in ('AT_V2_ISOLATED_STAGING','AT_V2_TEST_DATABASE_DISPOSABLE',
                    'AT_V2_MILEAGE_ENGINE_TEST_ENABLED','AT_V2_PRODUCTION_MILEAGE_ENABLED','AT_V2_PROGRESSION_ENABLED'):
            self.assertFalse(activation_allowed({**config,key:'YES'}))
        self.assertFalse(activation_allowed({**config,'RAILWAY_GIT_COMMIT_SHA':'e'*40}))

    async def test_disabled_worker_cannot_open_database_or_fetch_roster(self):
        with patch.dict(os.environ,{},clear=True), patch('trucky_membership_adapter.fetch_current_member_ids',AsyncMock()) as roster:
            self.assertEqual((await allocate_saved_linked_history(None))['status'],'disabled')
            roster.assert_not_awaited()

    def test_fractional_history_and_replays_are_stable(self):
        row=observation()
        one=vault_candidate([row],789)
        two=vault_candidate([row,row],789)
        self.assertEqual(one,two)
        self.assertEqual(one['miles'], KM_TO_MILES.quantize(Decimal('.0000000001')))

    def test_export_and_account_conflicts_cannot_be_credited(self):
        self.assertIsNone(vault_candidate([{**observation(),'source_kind':'owner_export'}],789))
        self.assertIsNone(vault_candidate([observation(),observation(account='999')],789))
        self.assertIsNone(vault_candidate([observation(),observation(km='2')],789))
        self.assertIsNone(vault_candidate([observation(),observation(job='702')],789))

    def test_invalid_incomplete_and_future_records_remain_pending(self):
        for change in ({'distance_unit':'mi'},{'status':'started'},{'driven_distance_km':-1},
                       {'completed_at':'2999-01-01T00:00:00Z'},{'game_id':None},{'stats_type':'road_trip'}):
            self.assertIsNone(vault_candidate([observation(**change)],789))

    def test_ats_speed_policy_is_reused_not_rounded(self):
        for speed,expected in (('130','real'),('132','race'),('131',None),('190',None)):
            c=vault_candidate([observation(game_id=2,max_speed_kmh=speed)],789)
            self.assertEqual(c['mileage_type'] if c else None,expected)

    def test_missing_new_field_can_enrich_but_conflicting_speed_cannot(self):
        a=observation(game_id=2)
        b=observation(game_id=2,max_speed_kmh='130')
        self.assertIsNotNone(vault_candidate([a,b],789))
        self.assertIsNone(vault_candidate([b,observation(game_id=2,max_speed_kmh='132')],789))


try:
    import asyncpg
except ImportError:
    asyncpg=None

@unittest.skipUnless(asyncpg is not None and os.getenv('AT_V2_TEST_DATABASE_DISPOSABLE')=='YES'
    and '/v2_test' in os.getenv('TEST_DATABASE_URL',''), 'Requires disposable PostgreSQL')
class LinkedAllocationDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from urllib.parse import urlsplit
        from unified_driver_profiles import SCHEMA as PROFILE
        from unified_production_ledger_adapter_v2 import SCHEMA as LEDGER
        parsed=urlsplit(os.environ['TEST_DATABASE_URL'])
        if parsed.hostname not in ('127.0.0.1','localhost','::1') or parsed.path!='/v2_test':
            self.skipTest('Local disposable v2_test only')
        self.schema='linked_test_'+uuid.uuid4().hex
        self.admin=await asyncpg.connect(os.environ['TEST_DATABASE_URL'])
        await self.admin.execute('CREATE SCHEMA '+self.schema)
        self.pool=await asyncpg.create_pool(os.environ['TEST_DATABASE_URL'],min_size=1,max_size=4,
                                           server_settings={'search_path':self.schema})
        async with self.pool.acquire() as c:
            await c.execute(PROFILE)
            await c.execute(LEDGER)
            await c.execute('''CREATE TABLE driver_progress(discord_user_id BIGINT PRIMARY KEY,
                trucksbook_name TEXT UNIQUE NOT NULL,real_miles BIGINT DEFAULT 0,updated_at TIMESTAMPTZ DEFAULT NOW());
                CREATE TABLE driver_links(discord_user_id BIGINT PRIMARY KEY);
                CREATE TABLE processed_jobs(job_id TEXT PRIMARY KEY,discord_user_id BIGINT);
                INSERT INTO unified_driver_profiles(discord_user_id,status) VALUES(789,'active');
                INSERT INTO unified_steam_links(discord_user_id,steam_id64) VALUES(789,'76561198000000001');
                INSERT INTO unified_provider_links(provider,provider_account_id,discord_user_id)
                  VALUES('trucky','456',789);
                INSERT INTO unified_registration_sessions(discord_user_id,status) VALUES(789,'approved');''')

    async def asyncTearDown(self):
        await self.pool.close()
        await self.admin.execute('DROP SCHEMA '+self.schema+' CASCADE')
        await self.admin.close()

    async def settle(self, row=None):
        async with self.pool.acquire() as c:
            return await _settle_candidate(c,vault_candidate([row or observation()],789),{'456'})

    async def test_linked_not_verified_is_accepted_and_registration_unchanged(self):
        self.assertEqual(await self.settle(),'credited_real')
        async with self.pool.acquire() as c:
            self.assertIsNone(await c.fetchval('SELECT verified_at FROM unified_provider_links'))
            self.assertEqual(await c.fetchval('SELECT status FROM unified_registration_sessions'),'approved')
            self.assertEqual(await c.fetchval('SELECT real_miles FROM driver_progress'),0)
        self.assertEqual(await self.settle(observation(job='702')),'credited_real')
        async with self.pool.acquire() as c:
            self.assertEqual(await c.fetchval('SELECT real_miles FROM driver_progress'),1)

    async def test_concurrent_retry_credits_once(self):
        result=await asyncio.gather(*(self.settle() for _ in range(4)))
        self.assertEqual(result.count('credited_real'),1)
        self.assertEqual(result.count('replayed'),3)
        async with self.pool.acquire() as c:
            self.assertEqual(await c.fetchval('SELECT COUNT(*) FROM unified_v2_production_credit_ledger'),1)

    async def test_legacy_balances_preserved_race_separate_real_held(self):
        async with self.pool.acquire() as c:
            await c.execute("INSERT INTO driver_progress(discord_user_id,trucksbook_name,real_miles) VALUES(789,'existing',122038)")
        self.assertEqual(await self.settle(),'pending_reconciliation')
        self.assertEqual(await self.settle(observation(kind='race_miles')),'credited_race')
        async with self.pool.acquire() as c:
            self.assertEqual(await c.fetchval('SELECT real_miles FROM driver_progress'),122038)
            self.assertGreater(await c.fetchval('SELECT trucky_race_miles FROM unified_v2_production_mileage_totals'),0)

    async def test_tb_link_or_historical_jobs_hold_real_even_with_zero_balance(self):
        async with self.pool.acquire() as c:
            await c.execute('INSERT INTO driver_links VALUES(789)')
        self.assertEqual(await self.settle(),'pending_reconciliation')
        async with self.pool.acquire() as c:
            await c.execute('DELETE FROM driver_links')
            await c.execute("INSERT INTO processed_jobs VALUES('old',789)")
        self.assertEqual(await self.settle(),'pending_reconciliation')

    async def test_account_membership_registration_and_steam_required(self):
        async with self.pool.acquire() as c:
            candidate=vault_candidate([observation()],789)
            self.assertEqual(await _settle_candidate(c,candidate,set()),'account_link_pending')
            await c.execute("UPDATE unified_registration_sessions SET status='pending'")
        self.assertEqual(await self.settle(),'account_link_pending')

    async def test_rollback_leaves_no_credit_or_partial_total(self):
        async with self.pool.acquire() as c:
            await c.execute("""CREATE FUNCTION fail_decision() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'injected failure'; END; $$;
                CREATE TRIGGER fail_decision BEFORE INSERT ON unified_v2_production_mileage_decisions
                FOR EACH ROW EXECUTE FUNCTION fail_decision();""")
        with self.assertRaises(Exception):
            await self.settle()
        async with self.pool.acquire() as c:
            self.assertEqual(await c.fetchval('SELECT COUNT(*) FROM unified_v2_production_credit_ledger'),0)
            self.assertEqual(await c.fetchval('SELECT COUNT(*) FROM driver_progress'),0)
            self.assertEqual(await c.fetchval('SELECT COUNT(*) FROM unified_v2_production_mileage_totals'),0)
