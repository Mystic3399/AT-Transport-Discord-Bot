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
    async def fetchval(self,sql,user): return 2
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
