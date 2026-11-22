"""Regression checks for verifiable missing-mileage diagnostics and tachograph."""
import ast
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
from decimal import Decimal
from driver_mileage_diagnostics_v2 import inspect_driver,format_driver_report

ROOT=Path(__file__).resolve().parents[1]

class DriverEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_numeric_trucksbook_id_resolves_saved_uncredited_jobs(self):
        class Conn:
            fetchrow=AsyncMock(return_value={
                'legacy_tb_id':'531098','legacy_tb_name':'A&T luke plant',
                'verified_tb_id':None,'completed_tb_id':None,
                'verified_trucky_id':None,'verification_status':'completed',
                'profile_real':0,'processed_real_jobs':0,'processed_real_miles':0,
                'credited_trucky_real':Decimal(0),'credited_trucky_race':Decimal(0)
            })
            fetch=AsyncMock(side_effect=[
                [{'provider_account_id':'531098','source_jobs':3,
                  'uncredited_real_sources':3,'processed_to_other_driver':0}],
                [{'provider_account_id':'531098','reason_code':'unverified_owner',
                  'state':'awaiting_account_link','jobs':3}]
            ])
        conn=Conn()
        evidence=await inspect_driver(conn,42)
        self.assertEqual(evidence['tb_accounts'],['531098'])
        self.assertEqual(evidence['source_jobs_by_id'][0]['uncredited_real_sources'],3)
        self.assertEqual(conn.fetch.await_args_list[0].args[1],['531098'])
        self.assertEqual(conn.fetch.await_args_list[0].args[2],42)
        report=format_driver_report(42,evidence,company_confirmed=True)
        self.assertIn('531098',report)
        self.assertIn('3 uncredited Real job(s)',report)
        self.assertIn('Verified',report)
        self.assertIn('Diagnostic only',report)

    async def test_name_only_not_marked_as_an_authenticated_link(self):
        class Conn:
            fetchrow=AsyncMock(return_value={
                'legacy_tb_id':None,'legacy_tb_name':'A&T luke plant',
                'verified_tb_id':None,'completed_tb_id':None,
                'profile_real':0,'processed_real_jobs':0,'processed_real_miles':0})
            fetch=AsyncMock()
        conn=Conn()
        evidence=await inspect_driver(conn,42)
        self.assertEqual(evidence['tb_accounts'],[])
        conn.fetch.assert_not_awaited()
        self.assertIn('name-only record',format_driver_report(42,evidence))
        self.assertIn('do not prove account ownership',format_driver_report(42,evidence))

    async def test_conflicting_ids_are_displayed_as_conflict_not_merged(self):
        class Conn:
            fetchrow=AsyncMock(return_value={
                'legacy_tb_id':'531098','verified_tb_id':'549999',
                'completed_tb_id':None,'profile_real':0})
            fetch=AsyncMock(side_effect=[[],[]])
        evidence=await inspect_driver(Conn(),42)
        self.assertTrue(evidence['tb_account_conflict'])
        self.assertIn('Conflicting saved TrucksBook IDs',format_driver_report(42,evidence))

    def test_zero_mile_profile_reads_real_wotr_and_requires_number_for_tick(self):
        code=(ROOT/'bot.py').read_text()
        tree=ast.parse(code)
        fn=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef)
                and n.name=='fetch_driver_profile_stats')
        snippet=ast.unparse(fn)
        self.assertIn("LOWER(statistics) LIKE 'real%'",snippet)
        self.assertIn("LOWER(p.statistics) LIKE 'real%'",snippet)
        self.assertIn("links.trucksbook_user_id IS NOT NULL",snippet)
        self.assertIn('has_tb_name',snippet)
        self.assertIn('ID verification required',snippet)

    async def test_startup_scan_never_guesses_or_writes_provider_ownership(self):
        from driver_mileage_diagnostics_v2 import scan_zero_mile_driver_sources
        class Conn:
            fetch=AsyncMock(return_value=[{
                'discord_user_id':42,'trucksbook_name':'A&T Luke plant',
                'tb_id':'531098','real_miles':0,'source_jobs':3,
                'uncredited_real_sources':3,'processed_jobs':0
            }])
        conn=Conn()
        rows=await scan_zero_mile_driver_sources(conn)
        self.assertEqual(rows[0]['tb_id'],'531098')
        sql=conn.fetch.await_args.args[0]
        self.assertIn('h.provider_account_id=l.tb_id',sql)
        self.assertIn("v.status='completed'",sql)
        self.assertIn("v.discord_user_id=ids.discord_user_id",sql)
        self.assertIn("($2::boolean OR",sql)
        self.assertFalse(conn.fetch.await_args.args[2])
        self.assertNotIn('UPDATE',sql)
        self.assertNotIn('INSERT',sql)
        self.assertNotIn('trucksbook_name=',sql)

    def test_startup_zero_mile_audit_is_diagnostic_only(self):
        code=(ROOT/'bot.py').read_text()
        fn=next(n for n in ast.parse(code).body
                if isinstance(n,ast.AsyncFunctionDef)
                   and n.name=='audit_zero_mile_driver_sources_once')
        snippet=ast.unparse(fn)
        self.assertIn('scan_zero_mile_driver_sources',snippet)
        self.assertIn('LINKED DRIVER SOURCE SNAPSHOT',snippet)
        self.assertNotIn('record_real_job',snippet)
        self.assertNotIn('UPDATE driver_links',snippet)

    def test_admin_command_is_read_only_and_uses_explicit_mention(self):
        code=(ROOT/'bot.py').read_text()
        fn=next(n for n in ast.parse(code).body if isinstance(n,ast.AsyncFunctionDef)
                and n.name=='mileage_driver_diagnostic')
        txt=ast.unparse(fn)
        self.assertIn('has_guild_permissions(administrator=True)',txt)
        self.assertIn('inspect_driver',txt)
        self.assertIn('discord.Member',txt)
        self.assertNotIn('record_real_job',txt)
        self.assertNotIn('UPDATE driver_links',txt)
        self.assertNotIn('INSERT INTO processed_jobs',txt)
