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
    def test_registration_only_production_gate_requires_exact_approved_deployment(self):
        sha = 'a' * 40
        env = {'RAILWAY_ENVIRONMENT_ID': workflow.PRODUCTION_ENVIRONMENT,
               'RAILWAY_PROJECT_ID': workflow.PROJECT_ID,
               'RAILWAY_SERVICE_ID': workflow.PRODUCTION_SERVICE,
               'RAILWAY_GIT_COMMIT_SHA': sha,
               'AT_V2_REGISTRATION_ENABLED': 'YES',
               'AT_V2_REGISTRATION_APPROVED_COMMIT': sha}
        self.assertTrue(workflow.enabled(env))
        from unified_mileage_engine_control_v2 import mileage_engine_enabled
        from unified_production_mileage_gate_v2 import production_activation_blockers
        self.assertFalse(mileage_engine_enabled(env))
        self.assertIn('production_activation_disabled', production_activation_blockers(env))
        for key in env:
            with self.subTest(missing=key):
                candidate = dict(env); candidate.pop(key)
                self.assertFalse(workflow.enabled(candidate))
            with self.subTest(wrong=key):
                self.assertFalse(workflow.enabled({**env, key: 'wrong'}))
        for flag in ('AT_V2_ISOLATED_STAGING', 'AT_V2_STAGING_REGISTRATION_ENABLED'):
            self.assertFalse(workflow.enabled({**env, flag: 'YES'}))
        self.assertFalse(workflow.enabled({**env, 'RAILWAY_GIT_COMMIT_SHA': 'b' * 40}))
        # Even a matching short/non-hex SHA must fail closed.
        for invalid in ('abc', 'z' * 40):
            self.assertFalse(workflow.enabled({**env, 'RAILWAY_GIT_COMMIT_SHA': invalid,
                                              'AT_V2_REGISTRATION_APPROVED_COMMIT': invalid}))

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

class StagingSlashSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_register_sync_is_guild_only_and_idempotent(self):
        tree=ast.parse(Path('bot.py').read_text())
        node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='sync_staging_registration_commands')
        copy=unittest.mock.Mock()
        sync=AsyncMock(return_value=['register','myregistration'])
        ns=dict(os=os,GUILD_ID=123,staging_registration_commands_synced=False,
                discord=SimpleNamespace(Object=lambda **kwargs:SimpleNamespace(**kwargs)),
                bot=SimpleNamespace(tree=SimpleNamespace(copy_global_to=copy,sync=sync)))
        exec(compile(ast.Module(body=[node],type_ignores=[]),'bot.py','exec'),ns)
        env={**ENV,'STAGING_GUILD_ID':'123'}
        with patch.dict(os.environ,env,clear=True):
            await ns['sync_staging_registration_commands']()
            await ns['sync_staging_registration_commands']()
        sync.assert_awaited_once()
        self.assertEqual(sync.await_args.kwargs['guild'].id,123)
        self.assertTrue(ns['staging_registration_commands_synced'])
        # No global sync or application-command deletion is exposed to helper.
        self.assertEqual(copy.call_count,1)

    async def test_no_sync_outside_isolated_registration_or_wrong_guild(self):
        tree=ast.parse(Path('bot.py').read_text())
        node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='sync_staging_registration_commands')
        for env in ({}, {**ENV,'STAGING_GUILD_ID':'999'}):
            sync=AsyncMock()
            ns=dict(os=os,GUILD_ID=123,staging_registration_commands_synced=False,
                    bot=SimpleNamespace(tree=SimpleNamespace(sync=sync)))
            exec(compile(ast.Module(body=[node],type_ignores=[]),'bot.py','exec'),ns)
            with patch.dict(os.environ,env,clear=True):
                if env:
                    with self.assertRaises(ValueError): await ns['sync_staging_registration_commands']()
                else:
                    await ns['sync_staging_registration_commands']()
            sync.assert_not_awaited()

