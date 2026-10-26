"""Guided registration and applicant confirmation never invoke mileage/role writers."""
import ast
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import unified_registration_workflow_v2 as workflow
from tests.test_registration_workflow_v2 import ENV

class RegistrationConfirmationTests(unittest.IsolatedAsyncioTestCase):
    def view(self, steam='Verified', linked=True, ready=True, channel=22):
        return dict(steam_status=steam,accounts={'trucksbook':{'status':'Linked' if linked else 'Not Linked'},
                    'trucky':{'status':'Not Linked'}},ready=ready,
                    session={'verification_channel_id':channel})

    def command(self, owner=11, complete=True, view=None, delete_error=None):
        tree=ast.parse(Path('bot.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='confirm_verification')
        fn.decorator_list=[]
        class Forbidden(Exception):pass
        class NotFound(Exception):pass
        class HTTPException(Exception):pass
        discord=SimpleNamespace(Forbidden=Forbidden,NotFound=NotFound,HTTPException=HTTPException)
        channel=SimpleNamespace(id=22,delete=AsyncMock(side_effect=delete_error))
        ctx=SimpleNamespace(guild=SimpleNamespace(id=1),author=SimpleNamespace(id=11,bot=False),channel=channel,reply=AsyncMock())
        ns=dict(GUILD_ID=1,db_pool=object(),discord=discord,asyncio=SimpleNamespace(sleep=AsyncMock()),
                get_verification_by_channel=AsyncMock(return_value={'discord_user_id':owner}),
                refresh_unified_registration=AsyncMock(),audit_registration_account=AsyncMock(),print=lambda *a,**k:None)
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'bot.py','exec'),ns)
        return ctx,ns,discord

    async def run_command(self,ctx,ns,completed=True,view=None):
        with patch.dict(os.environ,ENV,clear=True),patch.object(workflow,'complete_if_ready',AsyncMock(return_value=completed)) as finish,patch.object(workflow,'state',AsyncMock(return_value=view or self.view())):
            await ns['confirm_verification'](ctx)
        return finish

    async def test_applicant_confirmation_closes_only_own_channel(self):
        ctx,ns,_=self.command()
        finish=await self.run_command(ctx,ns)
        finish.assert_awaited_once()
        ns['asyncio'].sleep.assert_awaited_once_with(30)
        ctx.channel.delete.assert_awaited_once()

    async def test_other_member_cannot_confirm(self):
        ctx,ns,_=self.command(owner=99)
        finish=await self.run_command(ctx,ns)
        finish.assert_not_awaited();ctx.channel.delete.assert_not_awaited()

    async def test_incomplete_registration_keeps_channel_with_instructions(self):
        ctx,ns,_=self.command()
        await self.run_command(ctx,ns,False,self.view(steam='Not Linked',linked=False,ready=False))
        ctx.channel.delete.assert_not_awaited()
        self.assertIn('!linksteam',ctx.reply.call_args.args[0])

    async def test_cancelled_or_changed_channel_is_not_deleted(self):
        for view in (self.view(ready=False),self.view(channel=33)):
            ctx,ns,_=self.command();await self.run_command(ctx,ns,view=view)
            ctx.channel.delete.assert_not_awaited()

    async def test_permission_error_reports_completion_without_data_changes(self):
        ctx,ns,d=self.command();ctx.channel.delete.side_effect=d.Forbidden()
        await self.run_command(ctx,ns)
        self.assertIn('Manage Channels',ctx.reply.call_args.args[0])

    def test_next_step_instructions_cover_steam_provider_and_confirm(self):
        self.assertIn('!linksteam',workflow.next_steps(self.view(steam='Not Linked',linked=False)))
        text=workflow.next_steps(self.view(linked=False))
        self.assertIn('!trucksbook',text);self.assertIn('!registertrucky',text)
        self.assertIn('!confirm',workflow.next_steps(self.view()))

class ProviderConfirmationTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_provider_links_show_matched_details_and_confirm_instruction(self):
        import asyncpg
        tree=ast.parse(Path('bot.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='link_registration_provider')
        class Embed:
            def __init__(self,**kwargs):self.title=kwargs['title'];self.description=kwargs['description'];self.fields=[]
            def add_field(self,**kwargs):self.fields.append(kwargs)
            def set_footer(self,**kwargs):self.footer=kwargs['text']
        discord=SimpleNamespace(Embed=Embed,Colour=SimpleNamespace(blue=lambda:1),
            utils=SimpleNamespace(escape_markdown=lambda x:x),AllowedMentions=SimpleNamespace(none=lambda:None))
        ns=dict(GUILD_ID=1,db_pool=object(),discord=discord,asyncpg=asyncpg,
            get_verification_by_channel=AsyncMock(return_value={'discord_user_id':11}),
            fetch_trucksbook_profile=AsyncMock(return_value={'success':True,'belongs_to_at':True,'trucksbook_name':'Matched Driver'}),
            trucky_membership_adapter=SimpleNamespace(verify_member=AsyncMock(return_value='Matched Driver')),
            refresh_unified_registration=AsyncMock(),audit_registration_account=AsyncMock())
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'bot.py','exec'),ns)
        for provider in ('trucksbook','trucky'):
            ctx=SimpleNamespace(guild=SimpleNamespace(id=1),author=SimpleNamespace(id=11,bot=False),channel=SimpleNamespace(id=22),reply=AsyncMock())
            with patch.object(workflow,'link_supported_account',AsyncMock()):
                await ns['link_registration_provider'](ctx,provider,'123')
            confirmation=ctx.reply.call_args.kwargs['embed']
            self.assertIn('!confirm',confirmation.description)
            self.assertIn({'name':'Account ID','value':'123','inline':True},confirmation.fields)
            self.assertTrue(any(f['value']=='Matched Driver' for f in confirmation.fields))
            self.assertTrue(any('not independently verified' in f['value'] for f in confirmation.fields))

class RegistrationAuditTests(unittest.IsolatedAsyncioTestCase):
    def audit(self, logger):
        tree=ast.parse(Path('bot.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='audit_registration_account')
        ns=dict(GUILD_ID=1,log_verification=logger,discord=SimpleNamespace(utils=SimpleNamespace(escape_markdown=lambda x:x)),print=lambda *a,**k:None)
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'bot.py','exec'),ns)
        ctx=SimpleNamespace(guild=SimpleNamespace(id=1),author=SimpleNamespace(id=11),channel=SimpleNamespace(id=22))
        return ns['audit_registration_account'],ctx

    async def test_link_attempt_and_result_use_existing_staff_log(self):
        logger=AsyncMock();audit,ctx=self.audit(logger)
        for event in ('attempt','linked','membership_unavailable','account_conflict','confirmed'):
            await audit(ctx,'trucksbook',event,'123','Matched Driver')
        self.assertEqual(logger.await_count,5)
        self.assertIn('Account ID',logger.call_args.args[2])
        self.assertIn('not independently verified',logger.call_args.args[2])

    async def test_invalid_input_and_unknown_outcomes_cannot_leak_secrets(self):
        logger=AsyncMock();audit,ctx=self.audit(logger)
        await audit(ctx,'trucky','invalid_id','secret-token-url')
        self.assertNotIn('secret-token-url',logger.call_args.args[2])
        logger.reset_mock()
        await audit(ctx,'trucky','secret-token-url','123')
        logger.assert_not_awaited()
        ctx.guild.id=2
        await audit(ctx,'trucky','linked','123')
        logger.assert_not_awaited()

    async def test_log_delivery_failure_does_not_undo_or_misreport_account_link(self):
        logger=AsyncMock(side_effect=RuntimeError('Discord unavailable'))
        audit,ctx=self.audit(logger)
        await audit(ctx,'trucky','linked','123')
        logger.assert_awaited_once()
