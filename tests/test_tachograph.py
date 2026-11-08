import asyncio
import ast
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from PIL import Image

import tachograph


def test_all_country_flags_decode_at_card_size():
    from zipfile import ZipFile
    with ZipFile(tachograph.FLAGS) as archive:
        for entry in tachograph.countries().values():
            with Image.open(BytesIO(archive.read(entry["flag"]))) as image:
                assert image.size == (99,54)


def test_rank_boundaries_missing_values_and_long_names():
    prefs = dict(truck_key=tachograph.truck_keys()[0], country_code="AU")
    for value in (0,4999,5000,74999,75000,99999,100000,999999,1000000,2000000):
        for side in ("front","back"):
            profile=dict(real_miles=value,trucky_race_miles=None)
            png=tachograph.render("Long name "*40,profile,prefs,side,role="Operations Manager "*20)
            assert Image.open(png).size==(1117,700)


def test_all_trucks_render_and_all_shield_states_decode():
    for key in tachograph.truck_keys():
        png=tachograph.render("Driver",{},dict(truck_key=key,country_code=None))
        assert Image.open(png).format == "PNG"
    for rank in tachograph.MILES:
        assert tachograph.badge(rank,False).size == tachograph.badge(rank,True).size


@pytest.mark.asyncio
async def test_existing_assignment_survives_changes_and_retries():
    conn=SimpleNamespace(execute=AsyncMock(),fetchrow=AsyncMock(return_value={
        "truck_key":"scania-s-r-next-gen", "country_code":"IE"}),fetch=AsyncMock())
    assert (await tachograph.appearance(conn,42))["truck_key"]=="scania-s-r-next-gen"
    conn.fetch.assert_not_awaited()
    assert "pg_advisory_xact_lock" in conn.execute.call_args.args[0]


@pytest.mark.asyncio
async def test_new_assignment_uses_least_used_trucks():
    keys=tachograph.truck_keys()
    conn=SimpleNamespace(execute=AsyncMock(),fetchrow=AsyncMock(side_effect=[
        None,dict(truck_key=keys[-1],country_code=None)]),fetch=AsyncMock(return_value=[
        dict(truck_key=key,total=1) for key in keys[:-1]]))
    result=await tachograph.appearance(conn,42)
    assert result["truck_key"]==keys[-1]
    assert conn.fetchrow.call_args.args[2]==keys[-1]


def load_bot_definitions():
    source=Path(__file__).resolve().parents[1]/"bot.py"
    tree=ast.parse(source.read_text())
    names={"TachographFlip","tachograph_view"}
    nodes=[node for node in tree.body if getattr(node,"name",None) in names]
    ns={"discord":discord}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),"bot.py","exec"),ns)
    return ns


@pytest.mark.asyncio
async def test_flip_routes_survive_restart_and_replace_same_message():
    ns=load_bot_definitions()
    flip=ns["TachographFlip"]
    item=flip(42,"back")
    restored=await flip.from_custom_id(None,None,item.template.fullmatch("at:tacho:42:back"))
    assert restored.user_id==42 and restored.side=="back"
    member=SimpleNamespace(id=42)
    class Acquire:
        async def __aenter__(self):
            return conn
        async def __aexit__(self,*args):
            pass
    conn=SimpleNamespace(transaction=lambda **kw: Acquire())
    pool=SimpleNamespace(acquire=lambda: Acquire())
    ns.update(db_pool=pool,GUILD_ID=1,fetch_driver_profile_stats=AsyncMock(return_value={}),
              make_tachograph=AsyncMock(return_value=BytesIO(b"png")))
    interaction=SimpleNamespace(guild_id=1,guild=SimpleNamespace(get_member=lambda uid:member),
        response=SimpleNamespace(defer=AsyncMock()),
        edit_original_response=AsyncMock(),followup=SimpleNamespace(send=AsyncMock()))
    await restored.callback(interaction)
    interaction.edit_original_response.assert_awaited_once()
    kwargs=interaction.edit_original_response.call_args.kwargs
    assert len(kwargs["view"].children)==1
    assert kwargs["view"].children[0].item.custom_id=="at:tacho:42:front"
    assert item.template.fullmatch("at:tacho:42:race") is None
    assert len(kwargs["attachments"])==1


@pytest.mark.parametrize("country,code", [("England","GB-ENG"),("Ireland","IE"),
    ("Australia","AU"),("  united   kingdom ","GB"),("Scotland","GB-SCT"),
    ("Wales","GB-WLS"),("Northern Ireland","GB-NIR"),("usa","US"),("clear","CLEAR"),("unknown","")])
def test_driver_country_input(country, code):
    assert tachograph.resolve_country(country) == code


def test_change_country_is_a_registered_slash_subcommand():
    from discord.ext import commands
    tree = ast.parse((Path(__file__).resolve().parents[1]/"bot.py").read_text())
    names = {"save_card_country","card_country","card_change","change_country","country_autocomplete"}
    nodes = [node for node in tree.body if getattr(node,"name",None) in names]
    bot = commands.Bot(command_prefix="!",intents=discord.Intents.none())
    ns = dict(bot=bot,discord=discord)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),"bot.py","exec"),ns)
    group = bot.tree.get_command("change")
    flag = group.get_command("country")
    assert flag.parameters[0].name == "country"
    assert flag.parameters[0].autocomplete
