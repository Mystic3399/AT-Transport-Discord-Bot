from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import pytest
import onboarding_flow as flow


def view(steam='Not Linked',tracker=False):
    return dict(steam_status=steam,accounts={
        'trucksbook':dict(status='Linked' if tracker else 'Not Linked',display_name='Driver',account_id='123'),
        'trucky':dict(status='Not Linked',display_name=None,account_id=None)})


def test_stages_only_show_the_next_required_action():
    assert flow.stage(view())==1
    assert '!linksteam' in flow.instruction(view()).description
    assert '!trucksbook' not in flow.instruction(view()).description
    assert flow.stage(view('Verified'))==2
    assert '!trucky YOUR_ID' in flow.instruction(view('Verified')).description
    assert flow.stage(view('Verified',True))==3
    assert '!confirm' in flow.instruction(view('Verified',True)).description
    assert '/change truck' in flow.completion().description


class DB:
    def __init__(self): self.phase=0;self.welcome=None;self.failure=None
    async def __aenter__(self): return self
    async def __aexit__(self,*args): pass
    transaction=lambda self:self
    acquire=lambda self:self
    async def execute(self,sql,*args):
        if 'SET welcome_message_id' in sql:self.welcome=args[2]
        if 'SET phase=' in sql:self.phase=args[2];self.failure=None
        if 'SET last_failure_key=' in sql:self.failure=args[2]
    async def fetchrow(self,*args):return dict(phase=self.phase,welcome_message_id=self.welcome)
    async def fetchval(self,*args):return self.failure


@pytest.mark.asyncio
async def test_welcome_then_steam_then_tracking_and_no_repeats_after_restart():
    db=DB();channel=SimpleNamespace(id=2)
    send=AsyncMock(return_value=SimpleNamespace(id=3))
    with patch.object(flow,'send',send):
        await flow.advance(db,channel,1,view())
        assert ['WELCOME' in c.args[1].title for c in send.call_args_list]==[True,False]
        await flow.advance(db,channel,1,view())
        assert send.await_count==2
        await flow.advance(db,channel,1,view('Verified'))
        assert 'STEAM VERIFIED' in send.call_args_list[2].args[1].title
        assert 'STEP 2' in send.call_args_list[3].args[1].title
        await flow.advance(db,channel,1,view('Verified',True))
        assert 'STEP 3' in send.call_args_list[-1].args[1].title
        assert send.await_count==5
        await flow.advance(db,channel,1,view('Verified',True))
        assert send.await_count==5


@pytest.mark.asyncio
async def test_failed_steam_callback_is_deduplicated():
    db=DB();channel=SimpleNamespace(id=2)
    with patch.object(flow,'send',AsyncMock()) as send:
        await flow.failure(db,channel,1,'expired','state1:expired')
        await flow.failure(db,channel,1,'expired','state1:expired')
        assert send.await_count==1
        assert '!linksteam' in send.call_args.args[1].description
