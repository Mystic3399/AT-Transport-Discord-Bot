"""Provider-neutral profile display, with disposable PostgreSQL query coverage."""
import ast
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit


def functions():
    tree=ast.parse(Path('bot.py').read_text())
    names={'fetch_driver_profile_stats','profile_linked_accounts'}
    ns={'os':os}
    exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names],type_ignores=[]),'bot.py','exec'),ns)
    return ns


class ProfileAccountLabelTests(unittest.TestCase):
    def test_old_profile_previews_keep_legacy_account_label(self):
        accounts=functions()['profile_linked_accounts']({'trucksbook_name':'Existing','trucksbook_user_id':'123'})
        self.assertEqual(accounts,[dict(provider='TrucksBook',name='Existing',account_id='123',status='Linked')])

    def test_trucky_only_and_dual_links_do_not_invent_accounts(self):
        fn=functions()['profile_linked_accounts']
        tr=dict(provider='Trucky',name='Hub Driver',account_id='789',status='Linked')
        tb=dict(provider='TrucksBook',name='TB Driver',account_id='123',status='Linked')
        self.assertEqual(fn({'linked_accounts':[tr]}),[tr])
        self.assertEqual(fn({'linked_accounts':[tb,tr]}),[tb,tr])
        self.assertEqual(tr['status'],'Linked')


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'Requires disposable TEST_DATABASE_URL')
class ProfileProviderDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        u=urlsplit(os.environ['TEST_DATABASE_URL'])
        if os.getenv('AT_V2_TEST_DATABASE_DISPOSABLE')!='YES' or u.hostname not in ('localhost','127.0.0.1','::1') or u.path!='/v2_test':
            self.skipTest('Only explicitly disposable localhost/v2_test is permitted')
        import asyncpg
        self.conn=await asyncpg.connect(os.environ['TEST_DATABASE_URL'])
        self.tx=self.conn.transaction()
        await self.tx.start()
        await self.conn.execute('''CREATE SCHEMA profile_provider_read_test;
            SET LOCAL search_path TO profile_provider_read_test;
            CREATE TABLE driver_links(discord_user_id BIGINT PRIMARY KEY,trucksbook_name TEXT,trucksbook_user_id BIGINT);
            CREATE TABLE driver_progress(discord_user_id BIGINT PRIMARY KEY,real_miles BIGINT);
            CREATE TABLE processed_jobs(discord_user_id BIGINT,statistics TEXT,accepted_distance INT);
            CREATE TABLE permanent_achievements(discord_user_id BIGINT,achievement_miles BIGINT);
            CREATE TABLE hall_of_fame(discord_user_id BIGINT,highest_tier TEXT,immortal_number INT);
            CREATE TABLE competition_wins(discord_user_id BIGINT,period_type TEXT);
            CREATE TABLE unified_provider_links(discord_user_id BIGINT,provider TEXT,provider_account_id TEXT,provider_display_name TEXT,verified_at TIMESTAMPTZ,UNIQUE(discord_user_id,provider));
            CREATE TABLE unified_driver_profiles(discord_user_id BIGINT PRIMARY KEY,status TEXT);
            CREATE TABLE unified_registration_sessions(discord_user_id BIGINT PRIMARY KEY,status TEXT);
            CREATE TABLE unified_steam_links(discord_user_id BIGINT PRIMARY KEY,verified_at TIMESTAMPTZ);
        ''')
        self.read=functions()['fetch_driver_profile_stats']
        self.env=patch.dict(os.environ,{'AT_V2_PROGRESSION_ENABLED':'NO'})
        self.env.start()

    async def asyncTearDown(self):
        if hasattr(self,'env'):self.env.stop()
        if hasattr(self,'tx'):await self.tx.rollback()
        if hasattr(self,'conn'):await self.conn.close()

    async def seed_trucky(self,status='approved',steam=True):
        await self.conn.execute("INSERT INTO unified_driver_profiles VALUES(101,'active')")
        await self.conn.execute('INSERT INTO unified_registration_sessions VALUES(101,$1)',status)
        if steam:await self.conn.execute('INSERT INTO unified_steam_links VALUES(101,NOW())')
        await self.conn.execute("INSERT INTO unified_provider_links VALUES(101,'trucky','789','Hub Driver',NULL)")

    async def test_trucky_only_completed_registration_renders_zero_without_creating_tb_or_mileage(self):
        await self.seed_trucky()
        async with self.conn.transaction(readonly=True):profile=await self.read(self.conn,101)
        self.assertEqual(profile['trucksbook_name'],'Hub Driver')
        self.assertIsNone(profile['trucksbook_user_id'])
        self.assertEqual(profile['real_miles'],0)
        self.assertEqual(profile['jobs_completed'],0)
        self.assertEqual(profile['linked_accounts'],[dict(provider='Trucky',name='Hub Driver',account_id='789',status='Linked')])
        self.assertEqual(await self.conn.fetchval('SELECT COUNT(*) FROM driver_links'),0)
        self.assertEqual(await self.conn.fetchval('SELECT COUNT(*) FROM driver_progress'),0)

    async def test_dual_provider_keeps_legacy_mileage_jobs_and_achievements(self):
        await self.seed_trucky()
        await self.conn.execute("INSERT INTO driver_links VALUES(101,'Legacy Driver',456)")
        await self.conn.execute('INSERT INTO driver_progress VALUES(101,40001)')
        await self.conn.execute("INSERT INTO processed_jobs VALUES(101,'Real',176)")
        await self.conn.execute('INSERT INTO permanent_achievements VALUES(101,40000)')
        async with self.conn.transaction(readonly=True):profile=await self.read(self.conn,101)
        self.assertEqual([a['provider'] for a in profile['linked_accounts']],['TrucksBook','Trucky'])
        self.assertEqual(profile['trucksbook_user_id'],'456')
        self.assertEqual(profile['real_miles'],40001)
        self.assertEqual(profile['jobs_completed'],1)
        self.assertEqual(profile['permanent_achievements'],(40000,))

    async def test_unfinished_registration_does_not_expose_claim_as_profile(self):
        await self.seed_trucky(status='pending')
        self.assertIsNone(await self.read(self.conn,101))

    async def test_trucky_only_without_verified_steam_is_not_registered(self):
        await self.seed_trucky(steam=False)
        self.assertIsNone(await self.read(self.conn,101))

    async def test_legacy_tb_profile_remains_available_without_steam_migration(self):
        await self.conn.execute("INSERT INTO driver_links VALUES(102,'Old Driver',456)")
        await self.conn.execute('INSERT INTO driver_progress VALUES(102,12345)')
        async with self.conn.transaction(readonly=True):profile=await self.read(self.conn,102)
        self.assertEqual(profile['real_miles'],12345)
        self.assertEqual(profile['linked_accounts'][0]['provider'],'TrucksBook')
        self.assertEqual(len(profile['linked_accounts']),1)
