"""Company IDs must not enter the personal-account linking path."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
import onboarding_flow

@pytest.mark.asyncio
@pytest.mark.parametrize('provider,company_id',[('trucksbook','229356'),('trucky','49041')])
async def test_company_number_is_explained_before_provider_or_database_write(provider,company_id):
    tree=ast.parse((Path(__file__).resolve().parents[1]/'bot.py').read_text())
    fn=next(node for node in tree.body if getattr(node,'name',None)=='link_registration_provider')
    ns=dict(GUILD_ID=1,db_pool=object(),get_verification_by_channel=AsyncMock(return_value={'discord_user_id':42}),
        audit_registration_account=AsyncMock(),onboarding_flow=onboarding_flow,
        fetch_trucksbook_profile=AsyncMock(),trucky_membership_adapter=SimpleNamespace(verify_member=AsyncMock()))
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'bot.py','exec'),ns)
    ctx=SimpleNamespace(guild=SimpleNamespace(id=1),author=SimpleNamespace(id=42,bot=False),channel=SimpleNamespace(id=99),reply=AsyncMock())
    await ns['link_registration_provider'](ctx,provider,company_id)
    assert 'company ID' in ctx.reply.call_args.kwargs['embed'].description
    assert 'personal profile number' in ctx.reply.call_args.kwargs['embed'].description
    ns['fetch_trucksbook_profile'].assert_not_awaited()
    ns['trucky_membership_adapter'].verify_member.assert_not_awaited()
