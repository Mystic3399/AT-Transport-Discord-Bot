from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import pytest
import account_unlink as unlink


def member(*roles,uid=2):
    return SimpleNamespace(id=uid,bot=False,guild=SimpleNamespace(owner_id=1),roles=[SimpleNamespace(name=n) for n in roles])


def test_unlink_is_owners_and_management_only():
    assert unlink.allowed(member(uid=1))
    assert unlink.allowed(member('Management'))
    assert unlink.allowed(member('Co-Owner | Chief Operator Officer'))
    assert not unlink.allowed(member('Recruitment Manager'))
    assert not unlink.allowed(member('Server Moderator'))
    assert not unlink.allowed(member('━━ 👑 LEADERSHIP & STAFF ━━'))


@pytest.mark.asyncio
async def test_target_resolves_a_private_registration_channel_to_its_driver():
    conn=SimpleNamespace(fetch=AsyncMock(return_value=[dict(discord_user_id=123456789012345678)]))
    guild=SimpleNamespace(get_member=lambda uid:None)
    assert await unlink.resolve_target(conn,guild,'<#223456789012345678>')==123456789012345678
    assert await unlink.resolve_target(conn,guild,'<@123456789012345678>')==123456789012345678
    with pytest.raises(ValueError): await unlink.resolve_target(conn,guild,'arbitrary-username')


class DB:
    acquire=lambda self:self
    transaction=lambda self:self
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    fetchrow=AsyncMock()
    fetchval=AsyncMock(return_value=True)
    def __init__(self):self.execute=AsyncMock()


@pytest.mark.asyncio
@pytest.mark.parametrize('scope',['trucksbook','trucky','steam','all'])
async def test_unlink_only_removes_selected_identity_tables_never_career_history(scope):
    db=DB()
    with patch.object(unlink,'snapshot',AsyncMock(return_value=({},'fingerprint','{}'))):
        await unlink.unlink(db,2,1,scope,'fingerprint')
    sql=[c.args[0].lower() for c in db.execute.call_args_list]
    assert 'insert into driver_account_unlink_audit' in sql[0]
    for forbidden in ('driver_progress','processed_jobs','permanent_achievements','hall_of_fame','credit_ledger','mileage_totals','driver_card_preferences'):
        assert not any(forbidden in query for query in sql)
    steam_deleted=any('delete from unified_steam_links' in q for q in sql)
    assert steam_deleted==(scope in {'steam','all'})
    assert db.execute.call_args_list[1].args[2]==(['trucksbook','trucky'] if scope in {'steam','all'} else [scope])


@pytest.mark.asyncio
async def test_changed_accounts_reject_stale_confirmation_before_any_writes():
    db=DB()
    with patch.object(unlink,'snapshot',AsyncMock(return_value=({},'new','{}'))):
        with pytest.raises(ValueError,match='changed'):
            await unlink.unlink(db,2,1,'all','old')
    db.execute.assert_not_awaited()


def test_management_slash_command_has_account_choices_and_permission_defaults():
    import ast
    from pathlib import Path
    import discord
    from discord.ext import commands
    tree=ast.parse((Path(__file__).resolve().parents[1]/'bot.py').read_text())
    nodes=[n for n in tree.body if getattr(n,'name',None) in {'UnlinkConfirmation','unlink_driver','unlink_driver_autocomplete'}]
    bot=commands.Bot(command_prefix='!',intents=discord.Intents.none())
    ns=dict(bot=bot,discord=discord,account_unlink=unlink)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'bot.py','exec'),ns)
    command=bot.tree.get_command('unlink')
    assert command.default_permissions.manage_guild
    assert [p.name for p in command.parameters]==['driver','account']
    assert len(command.parameters[1].choices)==4


def test_management_role_must_match_the_configured_staff_id():
    candidate=member('Management')
    candidate.roles[0].id=50
    assert unlink.allowed(candidate,{50})
    assert not unlink.allowed(candidate,{99})
