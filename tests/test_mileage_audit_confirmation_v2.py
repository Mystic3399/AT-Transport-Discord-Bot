import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from mileage_audit_confirmation_v2 import audit_source_allowed,explicit_audit_miles
from tests.test_wotr_hotfix import load_function,NS,SOURCE
import ast

class AuditCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirm_routes_to_existing_writer_without_scanning(self):
        target=object();invoke=AsyncMock()
        audit=load_function('mileage_audit',dict(NS,GUILD_ID=1,mileage_apply=target))
        ctx=SimpleNamespace(guild=SimpleNamespace(id=1),invoke=invoke)
        await audit(ctx,'CONFIRM')
        invoke.assert_awaited_once_with(target,confirmation='CONFIRM')
        invoke.reset_mock();ctx.guild.id=2
        await audit(ctx,'CONFIRM');invoke.assert_not_awaited()

    async def test_bad_arguments_do_not_scan_or_apply(self):
        reply=AsyncMock();invoke=AsyncMock()
        audit=load_function('mileage_audit',dict(NS,GUILD_ID=1))
        ctx=SimpleNamespace(guild=SimpleNamespace(id=1),reply=reply,invoke=invoke)
        await audit(ctx,'not-a-limit')
        reply.assert_awaited_once();invoke.assert_not_awaited()

    def test_admin_and_guild_checks_remain_on_both_entry_points(self):
        for name in ('mileage_audit','mileage_apply'):
            node=next(n for n in ast.parse(SOURCE).body if isinstance(n,ast.AsyncFunctionDef) and n.name==name)
            decorators=' '.join(ast.unparse(d) for d in node.decorator_list)
            self.assertIn('guild_only',decorators)
            self.assertIn('administrator=True',decorators)

class AuditSourceTests(unittest.TestCase):
    def test_explicit_sources_only(self):
        self.assertTrue(audit_source_allowed(123,{'TRUCKSBOOK_HISTORY_WEBHOOK_IDS':'123,456'}))
        self.assertTrue(audit_source_allowed(456,{'TRUCKSBOOK_WEBHOOK_ID':'456'}))
        for value in ('','123,,456','123,123','123,x','0,123','-1,123'):
            self.assertFalse(audit_source_allowed(123,{'TRUCKSBOOK_HISTORY_WEBHOOK_IDS':value}))
        self.assertFalse(audit_source_allowed(999,{'TRUCKSBOOK_WEBHOOK_ID':'123'}))
        self.assertFalse(audit_source_allowed(True,{'TRUCKSBOOK_WEBHOOK_ID':'1'}))

    def test_km_fractional_and_unknown_units_never_become_integer_miles(self):
        def embed(value):return SimpleNamespace(to_dict=lambda:{'fields':[{'name':'Details','value':'Accepted distance: '+value}]})
        for value in ('123 mi','123 mil','123 miles'):
            self.assertEqual(explicit_audit_miles(embed(value)),123)
        self.assertEqual(explicit_audit_miles(embed('1,234 mi')),1234)
        for value in ('123 km','123.5 mi','123','-123 mi'):
            self.assertIsNone(explicit_audit_miles(embed(value)))
