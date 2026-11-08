import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord.ext import tasks
import pytest
import tachograph


def test_decorative_roles_do_not_replace_company_job_title():
    def member(*names, owner=False):
        return SimpleNamespace(id=42,guild=SimpleNamespace(owner_id=42 if owner else 1),
            roles=[SimpleNamespace(name=n) for n in names])
    assert tachograph.company_role(member('━━ 👑 LEADERSHIP & STAFF ━━','Owner | Chief Executive Officer'))=='OWNER / DRIVER'
    assert tachograph.company_role(member('━━ 👑 LEADERSHIP & STAFF ━━','Co-Owner | Chief Operator Officer'))=='CO-OWNER / DRIVER'
    assert tachograph.company_role(member('━━ 👑 LEADERSHIP & STAFF ━━','A&T Centurion'))=='DRIVER'
    assert tachograph.company_role(member('Recruitment Manager'))=='RECRUITMENT MANAGER / DRIVER'


@pytest.mark.asyncio
@pytest.mark.parametrize('fail_global',[False,True])
async def test_command_sync_restores_definitions_even_after_discord_error(fail_global):
    tree=ast.parse((Path(__file__).resolve().parents[1]/'bot.py').read_text())
    names={'retry_application_commands','before_application_command_retry'}
    nodes=[n for n in tree.body if getattr(n,'name',None) in names]
    definitions=[SimpleNamespace(name=n) for n in ['register','myregistration','change']]
    local=definitions.copy()
    class Tree:
        get_commands=lambda self: local.copy()
        copy_global_to=lambda self,**kwargs: None
        clear_commands=lambda self,**kwargs: local.clear()
        add_command=lambda self,cmd,**kwargs: local.append(cmd)
        sync=AsyncMock(side_effect=[definitions,RuntimeError('Missing access') if fail_global else []])
    ns=dict(discord=discord,tasks=tasks,bot=SimpleNamespace(tree=Tree(),wait_until_ready=AsyncMock()),
        GUILD_ID=1,application_commands_synced=False,os=SimpleNamespace(getenv=lambda k:None))
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'bot.py','exec'),ns)
    await ns['retry_application_commands'].coro()
    assert local==definitions
    assert ns['application_commands_synced']==(not fail_global)


@pytest.mark.asyncio
async def test_account_name_refresh_only_updates_linked_ids_and_retains_failed_names():
    tree=ast.parse((Path(__file__).resolve().parents[1]/'bot.py').read_text())
    names={'refresh_card_account_names','before_card_account_names'}
    nodes=[n for n in tree.body if getattr(n,'name',None) in names]
    rows=[dict(discord_user_id=42,provider='trucksbook',provider_account_id='123'),
          dict(discord_user_id=42,provider='trucky',provider_account_id='456'),
          dict(discord_user_id=43,provider='trucksbook',provider_account_id='789')]
    conn=SimpleNamespace(fetch=AsyncMock(return_value=rows),execute=AsyncMock())
    class Context:
        async def __aenter__(self): return conn
        async def __aexit__(self,*args): pass
    conn.transaction=lambda:Context()
    ns=dict(tasks=tasks,db_pool=SimpleNamespace(acquire=lambda:Context()),
        os=SimpleNamespace(getenv=lambda k:None),bot=SimpleNamespace(wait_until_ready=AsyncMock()),
        trucky_membership_adapter=SimpleNamespace(fetch_current_member_names=AsyncMock(return_value={'456':'New Trucky Name'})),
        fetch_trucksbook_profile=AsyncMock(side_effect=[dict(success=True,trucksbook_name='New TB Name'),dict(success=False)]))
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'bot.py','exec'),ns)
    await ns['refresh_card_account_names'].coro()
    calls=conn.execute.call_args_list
    assert len(calls)==3
    assert calls[0].args[1:]==(42,'trucksbook','123','New TB Name')
    assert calls[1].args[1:]==(42,123,'New TB Name')
    assert calls[2].args[1:]==(42,'trucky','456','New Trucky Name')
    assert all('verified_at' not in c.args[0] and 'real_miles' not in c.args[0] for c in calls)
