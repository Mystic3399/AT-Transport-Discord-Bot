"""Run repair approvals against disposable localhost/v2_test only."""
import asyncio
import hashlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from urllib.parse import urlsplit
import asyncpg
import mileage_audit_confirmation_v2 as audit
from tests.test_wotr_hotfix import load_function,NS

@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'Requires disposable PostgreSQL')
class AuditDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        u=urlsplit(os.environ['TEST_DATABASE_URL'])
        if (os.getenv('AT_V2_TEST_DATABASE_DISPOSABLE')!='YES' or u.hostname not in ('localhost','127.0.0.1','::1') or u.path!='/v2_test'):
            self.skipTest('Only opted-in localhost/v2_test is permitted')
        self.env=patch.dict(os.environ,{'TRUCKSBOOK_HISTORY_WEBHOOK_IDS':'123,456','AT_V2_ISOLATED_STAGING':'NO','AT_V2_PRODUCTION_MILEAGE_ENABLED':'NO'})
        self.env.start();self.addCleanup(self.env.stop)
        self.pool=await asyncpg.create_pool(os.environ['TEST_DATABASE_URL'],min_size=1,max_size=4)
        self.addAsyncCleanup(self.pool.close)
        async with self.pool.acquire() as c:
            await c.execute('''CREATE TABLE IF NOT EXISTS driver_progress (
                discord_user_id BIGINT PRIMARY KEY,trucksbook_name TEXT NOT NULL UNIQUE,
                real_miles BIGINT NOT NULL DEFAULT 0,updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
                CREATE TABLE IF NOT EXISTS driver_links (
                discord_user_id BIGINT PRIMARY KEY,trucksbook_name TEXT NOT NULL,trucksbook_user_id BIGINT,
                linked_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
                CREATE TABLE IF NOT EXISTS processed_jobs (
                job_id TEXT PRIMARY KEY,discord_user_id BIGINT NOT NULL,trucksbook_name TEXT NOT NULL DEFAULT '',
                accepted_distance INTEGER NOT NULL DEFAULT 0,statistics TEXT NOT NULL DEFAULT '',processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
                CREATE TABLE IF NOT EXISTS mileage_repair_candidates (
                job_id TEXT PRIMARY KEY,discord_user_id BIGINT NOT NULL,trucksbook_name TEXT NOT NULL,
                recorded_distance INTEGER NOT NULL,actual_distance INTEGER NOT NULL,
                missing_distance INTEGER NOT NULL CHECK(missing_distance>0),source_channel_id BIGINT NOT NULL,
                source_message_id BIGINT NOT NULL,audited_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                applied_at TIMESTAMPTZ,applied_by BIGINT,repair_kind TEXT NOT NULL DEFAULT 'distance_correction',
                source_delivered_at TIMESTAMPTZ,source_webhook_id BIGINT,source_provider_account_id TEXT);''')
            await c.execute(audit.SCHEMA)
        self.user=990000000000000000+int(hashlib.sha256(self._testMethodName.encode()).hexdigest()[:8],16)
        self.job=str(self.user);self.channel=9910001;self.guild=9910002;self.reviewer=9910003
        await self.seed(self.user,self.job,105)
        self.addAsyncCleanup(self.cleanup_rows)
        self.ctx=SimpleNamespace(guild=SimpleNamespace(id=self.guild),channel=SimpleNamespace(id=self.channel),
            author=SimpleNamespace(id=self.reviewer),reply=AsyncMock(),send=AsyncMock())
        ns=dict(NS,GUILD_ID=self.guild,db_pool=self.pool,PROGRESSION_ROLES=[],VERIFIED_HISTORICAL_REAL_REPAIRS={},
            AuditPreviewError=audit.AuditPreviewError,validate_mileage_audit_preview=audit.validate_mileage_audit_preview,
            consume_mileage_audit_preview=audit.consume_mileage_audit_preview,sync_member_progression_role=AsyncMock(),
            sync_permanent_system_for_driver=AsyncMock(),announce_pending_repair_achievements=AsyncMock(return_value=(0,0)),
            refresh_driver_leaderboard=AsyncMock())
        self.apply=load_function('mileage_apply',ns)

    async def cleanup_rows(self):
        async with self.pool.acquire() as c:
            await c.execute('DELETE FROM mileage_audit_previews_v2 WHERE guild_id=$1 AND reviewer_id=$2',self.guild,self.reviewer)
            await c.execute('DELETE FROM mileage_repair_candidates WHERE discord_user_id=ANY($1::bigint[])',[self.user,self.user+1])
            await c.execute('DELETE FROM processed_jobs WHERE discord_user_id=ANY($1::bigint[])',[self.user,self.user+1])
            await c.execute('DELETE FROM driver_links WHERE discord_user_id=ANY($1::bigint[])',[self.user,self.user+1])
            await c.execute('DELETE FROM driver_progress WHERE discord_user_id=ANY($1::bigint[])',[self.user,self.user+1])

    async def seed(self,user,job,delta,account=None):
        account=user if account is None else account
        async with self.pool.acquire() as c:
            await c.execute('INSERT INTO driver_progress(discord_user_id,trucksbook_name,real_miles) VALUES($1,$2,1000)',user,str(user))
            await c.execute('INSERT INTO driver_links(discord_user_id,trucksbook_name,trucksbook_user_id) VALUES($1,$2,$3)',user,str(user),account)
            await c.execute('''INSERT INTO mileage_repair_candidates
                (job_id,discord_user_id,trucksbook_name,recorded_distance,actual_distance,missing_distance,
                 source_channel_id,source_message_id,repair_kind,source_delivered_at,source_webhook_id,source_provider_account_id)
                VALUES($1,$2,$3,0,$4,$4,22,33,'missing_real',NOW(),123,$5)''',job,user,str(user),delta,str(account))

    async def preview(self,ids=None):
        return await audit.create_mileage_audit_preview(self.pool,self.guild,self.channel,self.reviewer,[self.job] if ids is None else ids)

    async def total(self):
        async with self.pool.acquire() as c:return await c.fetchval('SELECT real_miles FROM driver_progress WHERE discord_user_id=$1',self.user)

    async def test_exact_driver_batch_only_and_repeated_confirmation(self):
        p=await self.preview();self.assertEqual(p['addition'],105)
        self.assertEqual(p['drivers'][0]['proposed_real_miles'],1105)
        self.assertEqual(await self.total(),1000)
        await self.seed(self.user+1,str(self.user+1),200,778)
        await self.apply(self.ctx,'CONFIRM');await self.apply(self.ctx,'CONFIRM')
        self.assertEqual(await self.total(),1105)
        async with self.pool.acquire() as c:
            self.assertEqual(await c.fetchval('SELECT real_miles FROM driver_progress WHERE discord_user_id=$1',self.user+1),1000)
            self.assertEqual(await c.fetchval('SELECT COUNT(*) FROM processed_jobs WHERE job_id=$1',self.job),1)

    async def test_missing_expired_or_wrong_reviewer_channel_cannot_confirm(self):
        await self.apply(self.ctx,'CONFIRM');self.assertEqual(await self.total(),1000)
        p=await self.preview()
        for field,other in [('reviewer',self.reviewer+1),('channel',self.channel+1),('guild',self.guild+1)]:
            async with self.pool.acquire() as c:
                async with c.transaction():
                    rows=await c.fetch('SELECT * FROM mileage_repair_candidates WHERE job_id=$1',self.job)
                    with self.assertRaises(audit.AuditPreviewError):
                        await audit.validate_mileage_audit_preview(c,other if field=='guild' else self.guild,
                            other if field=='channel' else self.channel,other if field=='reviewer' else self.reviewer,rows)
        async with self.pool.acquire() as c:await c.execute("UPDATE mileage_audit_previews_v2 SET expires_at=NOW()-INTERVAL '1 second' WHERE preview_id=$1",p['preview_id'])
        await self.apply(self.ctx,'CONFIRM');self.assertEqual(await self.total(),1000)

    async def test_changed_candidate_rejected_before_any_driver_write(self):
        await self.preview()
        async with self.pool.acquire() as c:await c.execute('UPDATE mileage_repair_candidates SET actual_distance=106,missing_distance=106 WHERE job_id=$1',self.job)
        await self.apply(self.ctx,'CONFIRM');self.assertEqual(await self.total(),1000)

    async def test_changed_balance_or_link_requires_a_fresh_preview(self):
        await self.preview()
        async with self.pool.acquire() as c:await c.execute('UPDATE driver_progress SET real_miles=1010 WHERE discord_user_id=$1',self.user)
        await self.apply(self.ctx,'CONFIRM');self.assertEqual(await self.total(),1010)
        await self.preview()
        async with self.pool.acquire() as c:await c.execute('UPDATE driver_links SET trucksbook_user_id=778 WHERE discord_user_id=$1',self.user)
        await self.apply(self.ctx,'CONFIRM');self.assertEqual(await self.total(),1010)

    async def test_concurrent_confirmations_credit_once(self):
        await self.preview()
        await asyncio.wait_for(asyncio.gather(self.apply(self.ctx,'CONFIRM'),self.apply(self.ctx,'CONFIRM')),10)
        self.assertEqual(await self.total(),1105)

    async def test_transaction_failure_rolls_back_and_retry_is_safe(self):
        p=await self.preview()
        self.apply.__globals__['consume_mileage_audit_preview']=AsyncMock(side_effect=RuntimeError('interrupted commit'))
        with self.assertRaises(RuntimeError):await self.apply(self.ctx,'CONFIRM')
        self.assertEqual(await self.total(),1000)
        async with self.pool.acquire() as c:
            self.assertIsNone(await c.fetchval('SELECT applied_at FROM mileage_audit_previews_v2 WHERE preview_id=$1',p['preview_id']))
            self.assertEqual(await c.fetchval('SELECT COUNT(*) FROM processed_jobs WHERE job_id=$1',self.job),0)
        self.apply.__globals__['consume_mileage_audit_preview']=audit.consume_mileage_audit_preview
        await self.apply(self.ctx,'CONFIRM');self.assertEqual(await self.total(),1105)

    async def test_unknown_source_or_account_stays_held(self):
        async with self.pool.acquire() as c:await c.execute('UPDATE mileage_repair_candidates SET source_provider_account_id=NULL WHERE job_id=$1',self.job)
        p=await self.preview();self.assertEqual(p['jobs'],0);self.assertEqual(p['held_jobs'],1)
        await self.apply(self.ctx,'CONFIRM');self.assertEqual(await self.total(),1000)

    async def test_staging_and_parallel_v2_writer_block_legacy_confirmation(self):
        await self.preview()
        for key in ('AT_V2_ISOLATED_STAGING','AT_V2_PRODUCTION_MILEAGE_ENABLED'):
            with patch.dict(os.environ,{key:'YES'}):await self.apply(self.ctx,'CONFIRM')
            self.assertEqual(await self.total(),1000)
