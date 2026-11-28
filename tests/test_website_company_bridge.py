import asyncio
import hashlib
import hmac
import json
import unittest
from types import SimpleNamespace
from website_company_bridge import collect_snapshot, signed_payload
class Context:
    def __init__(self,value): self.value=value
    async def __aenter__(self): return self.value
    async def __aexit__(self,*args): pass
class Conn:
    def transaction(self,**kwargs):
        assert kwargs == dict(isolation='repeatable_read',readonly=True)
        return Context(self)
    async def fetchval(self,sql,user=None): return False if user is None else 2
class Pool:
    def acquire(self): return Context(Conn())
class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_reconciled_profiles_and_excludes_bots_former_and_unlinked(self):
        members=[SimpleNamespace(id=i,bot=i==4) for i in range(1,6)]
        guild=SimpleNamespace(chunked=True,members=members)
        seen=[]
        async def profile(conn,user):
            seen.append(user)
            return None if user==5 else dict(real_miles=100,trucky_race_miles='20.5',jobs_completed=3)
        result=await collect_snapshot(Pool(),guild,profile,{3:dict(company='Former company driver')})
        self.assertEqual(seen,[1,2,5])
        self.assertEqual(result['profiles'],[dict(discordUserId=str(i),realMiles=100,raceMiles=20.5,completedJobs=5) for i in (1,2)])
        self.assertEqual(result['totals'],dict(activeDrivers=2,discordMembers=4,realMiles=200,raceMiles=41,completedJobs=10))
    async def test_invalid_totals_fail_closed(self):
        async def profile(*args): return dict(real_miles=10,trucky_race_miles='NaN',jobs_completed=1)
        with self.assertRaises(RuntimeError):
            await collect_snapshot(Pool(),SimpleNamespace(chunked=True,members=[SimpleNamespace(id=1,bot=False)]),profile,{})
    def test_signature_matches_website_contract(self):
        body,headers=signed_payload({'version':1},'test-only-secret',1234567890)
        expected=hmac.new(b'test-only-secret',f'POST\n/api/integrations/command-bridge\n1234567890\n{body}'.encode(),hashlib.sha256).hexdigest()
        self.assertEqual(headers['x-at-signature'],expected)

class CreditedConn(Conn):
    def __init__(self, rows):
        self.rows = rows

    async def fetchval(self, sql, user=None):
        return True if user is None else 2

    async def fetchrow(self, sql, user):
        assert 'b.trucksbook_real_miles' in sql
        assert 't.combined_real_miles' in sql
        assert 'game' not in sql.lower()
        return self.rows.get(user)

class CreditedPool:
    def __init__(self, rows):
        self.connection = CreditedConn(rows)

    def acquire(self):
        return Context(self.connection)

class AllGameBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_company_and_personal_totals_include_ats_from_both_providers(self):
        from decimal import Decimal
        # Retained TrucksBook baseline includes 100 ETS2 + 200 ATS miles.
        # The canonical Real ledger includes 40 ETS2 Trucky + 60 ATS Trucky
        # + 70 ATS TrucksBook miles, after the higher-provider decision.
        credited = dict(real_miles=Decimal('470'), trucky_race_miles=Decimal('85.5'))
        async def legacy_profile(*args):
            return dict(real_miles=100, trucky_race_miles='0', jobs_completed=6)
        guild = SimpleNamespace(chunked=True, members=[SimpleNamespace(id=1, bot=False)])
        result = await collect_snapshot(CreditedPool({1: credited}), guild, legacy_profile, {})
        self.assertEqual(result['totals']['realMiles'], 470)
        self.assertEqual(result['totals']['raceMiles'], 85.5)
        self.assertEqual(result['profiles'][0]['realMiles'], 470)
        self.assertEqual(result['profiles'][0]['raceMiles'], 85.5)
        self.assertEqual(result['totals']['realMiles'], sum(p['realMiles'] for p in result['profiles']))

    async def test_unmigrated_profile_keeps_existing_totals(self):
        async def legacy_profile(*args):
            return dict(real_miles=100, trucky_race_miles='0', jobs_completed=1)
        guild = SimpleNamespace(chunked=True, members=[SimpleNamespace(id=1, bot=False)])
        result = await collect_snapshot(CreditedPool({}), guild, legacy_profile, {})
        self.assertEqual(result['profiles'][0]['realMiles'], 100)
        self.assertEqual(result['totals']['realMiles'], 100)

    async def test_invalid_canonical_mileage_is_rejected(self):
        async def legacy_profile(*args):
            return dict(real_miles=100, trucky_race_miles='0', jobs_completed=1)
        guild = SimpleNamespace(chunked=True, members=[SimpleNamespace(id=1, bot=False)])
        with self.assertRaises((RuntimeError, ValueError, OverflowError)):
            await collect_snapshot(CreditedPool({1: dict(real_miles=-1, trucky_race_miles=0)}), guild, legacy_profile, {})
