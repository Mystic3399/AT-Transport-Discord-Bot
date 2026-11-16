import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import discord
from discord.ext import commands
import pytest
import tachograph


def test_fleet_names_resolve_only_to_available_artwork():
    assert len(tachograph.available_trucks())==18
    for key,label in tachograph.available_trucks().items():
        assert tachograph.resolve_truck(label)==key
        assert tachograph.resolve_truck(key)==key
    assert tachograph.resolve_truck('../../other-driver')==''


@pytest.mark.asyncio
@pytest.mark.parametrize('registered',[False,True])
async def test_driver_selects_only_their_own_persisted_truck(registered):
    tree=ast.parse((Path(__file__).resolve().parents[1]/'bot.py').read_text())
    nodes=[n for n in tree.body if getattr(n,'name',None) in {'card_change','change_truck','truck_autocomplete'}]
    conn=SimpleNamespace(execute=AsyncMock())
    class Context:
        async def __aenter__(self): return conn
        async def __aexit__(self,*args): pass
    conn.transaction=lambda:Context()
    mock_art=SimpleNamespace(resolve_truck=tachograph.resolve_truck,available_trucks=tachograph.available_trucks,appearance=AsyncMock())
    bot=commands.Bot(command_prefix='!',intents=discord.Intents.none())
    ns=dict(bot=bot,discord=discord,tachograph=mock_art,GUILD_ID=1,
        db_pool=SimpleNamespace(acquire=lambda:Context()),
        fetch_driver_profile_stats=AsyncMock(return_value={} if registered else None))
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'bot.py','exec'),ns)
    command=bot.tree.get_command('change').get_command('truck')
    assert command.parameters[0].autocomplete
    ctx=SimpleNamespace(guild=SimpleNamespace(id=1),author=SimpleNamespace(id=42),interaction=None,reply=AsyncMock())
    await ns['change_truck'].callback(ctx,truck='Volvo FH6')
    if registered:
        assert conn.execute.call_args.args[1:]==(42,'volvo-fh6')
        assert 'country_code' not in conn.execute.call_args.args[0]
    else:
        conn.execute.assert_not_awaited()
        mock_art.appearance.assert_not_awaited()
