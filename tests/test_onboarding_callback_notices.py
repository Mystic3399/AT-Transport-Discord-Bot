from unittest.mock import AsyncMock,patch
from types import SimpleNamespace
import os
import pytest
from aiohttp.test_utils import TestClient,TestServer
import steam_callback_v2 as callback
import trucky_staging_receiver_v2 as receiver
from tests.test_registration_workflow_v2 import ENV


@pytest.mark.asyncio
@pytest.mark.parametrize('case',['cancelled','expired','unknown'])
async def test_failed_steam_attempt_notifies_only_an_identified_driver(case):
    state='a'*48
    record=None if case=='unknown' else dict(discord_user_id=42,expected_return_to='https://example.test/auth/steam/callback?state='+state,valid=case!='expired',consumed_at=None)
    conn=SimpleNamespace(fetchrow=AsyncMock(return_value=record))
    class Context:
        async def __aenter__(self):return conn
        async def __aexit__(self,*args):pass
    pool=SimpleNamespace(acquire=lambda:Context())
    failed=AsyncMock();verified=AsyncMock()
    client=TestClient(TestServer(receiver.create_integrations_app(pool,on_steam_failed=failed,on_steam_verified=verified)))
    await client.start_server()
    try:
        with patch.dict(os.environ,{**ENV,'AT_STEAM_CALLBACK_URL':'https://example.test/auth/steam/callback'},clear=True),patch.object(callback.unified_driver_profiles,'bind_verified_steam',AsyncMock()) as bind:
            response=await client.get('/auth/steam/callback',params={'state':state,'openid.mode':'cancel'})
            assert response.status==400
            bind.assert_not_awaited()
        verified.assert_not_awaited()
        if case=='unknown':failed.assert_not_awaited()
        else:
            assert failed.call_args.args[0:2]==(42,case)
            assert state not in failed.call_args.args[2]
    finally:await client.close()
