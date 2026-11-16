import ast
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import pytest
from PIL import Image
import onboarding_flow as flow
import steam_openid_v2


def test_realistic_openid_url_is_clickable_without_a_discord_url_button():
    url=steam_openid_v2.make_login_url('https://at-transport-discord-bot-production.up.railway.app/auth/steam/callback?state='+'a'*64,
        'https://at-transport-discord-bot-production.up.railway.app')
    assert len(url)>512
    embed=flow.steam_signin(url)
    assert f']({url})' in embed.description
    assert len(embed.description)<4096


def test_onboarding_logo_is_small_png():
    image=Image.open(BytesIO(flow.logo_png()))
    assert image.format=='PNG'
    assert image.width<=144 and image.height<=64


@pytest.mark.asyncio
async def test_linksteam_sends_the_long_link_in_the_applicants_private_room():
    tree=ast.parse((Path(__file__).resolve().parents[1]/'bot.py').read_text())
    fn=next(n for n in tree.body if getattr(n,'name',None)=='link_steam_for_registration')
    fn.decorator_list=[]
    base='https://example.test'
    ns=dict(GUILD_ID=1,db_pool=object(),os=SimpleNamespace(getenv=lambda name,default='':base+'/auth/steam/callback' if name=='AT_STEAM_CALLBACK_URL' else base),
        get_verification_by_channel=AsyncMock(return_value={'discord_user_id':42}),
        refresh_unified_registration=AsyncMock(),onboarding_flow=flow,steam_openid_v2=steam_openid_v2,
        unified_driver_profiles=SimpleNamespace(create_steam_auth_state=AsyncMock(return_value=base+'/auth/steam/callback?state='+'b'*64)))
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'bot.py','exec'),ns)
    ctx=SimpleNamespace(guild=SimpleNamespace(id=1),author=SimpleNamespace(id=42,bot=False),channel=SimpleNamespace(id=99),reply=AsyncMock())
    await ns['link_steam_for_registration'](ctx)
    kwargs=ctx.reply.call_args.kwargs
    assert 'steamcommunity.com/openid/login?' in kwargs['embed'].description
    assert 'view' not in kwargs
