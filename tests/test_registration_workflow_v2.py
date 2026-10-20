"""Ordinary registration does not attest provider/job ownership or award mileage."""
import ast
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import unified_registration_workflow_v2 as workflow

ENV = {'AT_V2_ISOLATED_STAGING':'YES', 'AT_V2_STAGING_REGISTRATION_ENABLED':'YES',
       'RAILWAY_ENVIRONMENT_ID':workflow.STAGING_ENVIRONMENT}

class RegistrationPolicyTests(unittest.TestCase):
    def test_activation_is_explicit_and_staging_only(self):
        self.assertTrue(workflow.enabled(ENV))
        for key in ENV:
            env = dict(ENV); env.pop(key)
            self.assertFalse(workflow.enabled(env))
        self.assertFalse(workflow.enabled({**ENV, 'RAILWAY_ENVIRONMENT_ID':'production'}))

    def test_each_account_status_respects_available_evidence(self):
        link = {'provider_account_id':'123','verified_at':None}
        claim = {'provider_account_id':'123','status':'pending','ownership_confirmed':False,'ownership_evidence_kind':None}
        self.assertEqual(workflow.account_status(None,None),'Not Linked')
        self.assertEqual(workflow.account_status(None,claim),'Pending')
        self.assertEqual(workflow.account_status(link,claim),'Linked')
        link['verified_at'] = datetime.now(timezone.utc)
        self.assertEqual(workflow.account_status(link,claim),'Linked')
        claim.update(status='approved', ownership_confirmed=True,
                     ownership_evidence_kind='trucky_signed_job_steam_match')
        self.assertEqual(workflow.account_status(link,claim),'Verified')
        claim['provider_account_id']='456'
        self.assertEqual(workflow.account_status(link,claim),'Linked')


class RegistrationEmbedTests(unittest.IsolatedAsyncioTestCase):
    async def test_steam_callback_refresh_edits_durable_embed_without_other_writers(self):
        import asyncio
        tree=ast.parse(Path('bot.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='refresh_unified_registration')
        class Embed:
            def __init__(self, **kwargs): self.fields=[]; self.description=kwargs['description']
            def add_field(self,**kwargs): self.fields.append(kwargs)
            def set_footer(self,**kwargs): pass
        class Acquire:
            async def __aenter__(self): raise AssertionError('An existing embed edit must not write the database')
            async def __aexit__(self,*args): pass
        message=SimpleNamespace(edit=AsyncMock())
        channel=SimpleNamespace(guild=SimpleNamespace(id=11), fetch_message=AsyncMock(return_value=message),send=AsyncMock())
        fake_discord=SimpleNamespace(Embed=Embed,Colour=SimpleNamespace(blue=lambda:1),
            utils=SimpleNamespace(escape_markdown=lambda v:v),
            AllowedMentions=SimpleNamespace(none=lambda:None),NotFound=type('NotFound',(Exception,),{}))
        ns=dict(asyncio=asyncio,registration_embed_locks={},db_pool=SimpleNamespace(acquire=Acquire),
                bot=SimpleNamespace(get_channel=lambda channel_id:channel), GUILD_ID=11,discord=fake_discord)
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'bot.py','exec'),ns)
        view=dict(session={'verification_channel_id':22,'verification_message_id':33,'status':'pending'},
            steam_status='Verified',steam_id64='76561198000000072',ready=True,
            accounts={'trucksbook':dict(status='Linked',display_name='Test Driver',account_id='123'),
                      'trucky':dict(status='Not Linked',display_name=None,account_id=None)})
        with patch.dict(os.environ,ENV,clear=True),patch.object(workflow,'state',AsyncMock(return_value=view)):
            await ns['refresh_unified_registration'](111)
        channel.fetch_message.assert_awaited_once_with(33)
        channel.send.assert_not_awaited()
        embed=message.edit.await_args.kwargs['embed']
        self.assertEqual([f['name'] for f in embed.fields],['Steam — Verified','TrucksBook — Linked','Trucky — Not Linked'])
        self.assertIn('Registration complete',embed.description)
        self.assertIn('steamcommunity.com/profiles/',embed.fields[0]['value'])
