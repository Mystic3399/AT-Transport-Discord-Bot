"""Automatic pending-credit queue, both-provider eligibility and zero false payouts."""
import ast
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from automatic_mileage_holding_v2 import (
    STATES, company_eligible, membership_state, holding_state,
    safe_source_miles, store_state,
)

ROOT=Path(__file__).resolve().parents[1]

class MembershipRules(unittest.TestCase):
    def test_either_verified_at_provider_is_sufficient(self):
        for tb,tr in (('member','not_member'),('not_member','member'),
                      ('member','unknown'),('unknown','member')):
            self.assertTrue(company_eligible(trucksbook=tb,trucky=tr))
            self.assertEqual(membership_state(trucksbook=tb,trucky=tr),'eligible')

    def test_unknown_and_unlinked_not_paid(self):
        for tb,tr in (('unknown','unknown'),('not_member','not_member'),
                      ('unknown','not_member'),('not_member','unknown')):
            self.assertFalse(company_eligible(trucksbook=tb,trucky=tr))
            self.assertEqual(membership_state(trucksbook=tb,trucky=tr),
                             'awaiting_company_membership')
        self.assertFalse(company_eligible(trucksbook='member',discord_present=False))

    def test_reason_categorization_never_calls_pending_an_approved_credit(self):
        self.assertEqual(holding_state('account_link_pending'),'awaiting_account_link')
        self.assertEqual(holding_state('company_not_member'),'awaiting_company_membership')
        self.assertEqual(holding_state('historical_route_coverage_pending'),'awaiting_duplicate_evidence')
        self.assertEqual(holding_state('historical_balance_pending'),'awaiting_historical_baseline')
        self.assertEqual(holding_state('multiple_provider_owners'),'provider_conflict')
        self.assertEqual(holding_state('credited_race'),'credited')
        self.assertEqual(holding_state('credited_real'),'credited')
        self.assertEqual(holding_state('unrecognized_provider_record'),'awaiting_source_evidence')

    def test_source_miles_not_inferred_or_unbounded(self):
        self.assertIsNone(safe_source_miles(None))
        self.assertIsNone(safe_source_miles('NaN'))
        self.assertIsNone(safe_source_miles('-1'))
        self.assertIsNone(safe_source_miles(0))
        self.assertIsNone(safe_source_miles(1000001))
        self.assertEqual(str(safe_source_miles('125.125')),'125.125')

class QueueSafety(unittest.IsolatedAsyncioTestCase):
    async def test_pending_job_never_writes_mileage_or_discord_roles(self):
        conn=type('Conn',(),{})()
        conn.execute=AsyncMock()
        await store_state(conn,'trucky','100','123',mileage_type='race',
                          miles='155.125',reason='company_not_member')
        statement=conn.execute.await_args.args[0]
        self.assertIn("automatic_mileage_holding_v2",statement)
        self.assertIn("WHERE automatic_mileage_holding_v2.state<>'credited'",statement)
        self.assertNotIn("driver_progress",statement)
        self.assertNotIn("UPDATE unified_v2_production_mileage_totals",statement)
        self.assertEqual(conn.execute.await_args.args[6],'race')

    async def test_credited_status_needs_known_verified_driver(self):
        conn=type('Conn',(),{})()
        conn.execute=AsyncMock()
        with self.assertRaises(ValueError):
            await store_state(conn,'trucky','100','123',reason='credited')
        conn.execute.assert_not_awaited()

    async def test_invalid_job_id_refused(self):
        conn=type('Conn',(),{})()
        conn.execute=AsyncMock()
        for id_ in ('', '0', '1 OR 1=1', '-1'):
            with self.assertRaises(ValueError):
                await store_state(conn,'trucky',id_,'123')

class ReleasePathTests(unittest.TestCase):
    def test_trucky_queue_includes_unlinked_authenticated_history(self):
        text=(ROOT/'trucky_linked_allocation_v2.py').read_text()
        self.assertIn('LEFT JOIN unified_provider_links',text)
        self.assertIn('await store_state',text)
        self.assertIn('alternate_member_ids',text)
        self.assertIn('account_link_pending',text)
        self.assertIn('company_not_member',text)

    def test_trucksbook_replay_requires_current_company_eligibility(self):
        source=(ROOT/'bot.py').read_text()
        node=next(n for n in ast.parse(source).body if
                  isinstance(n,ast.AsyncFunctionDef) and n.name=='replay_saved_trucksbook_jobs')
        text=ast.unparse(node)
        self.assertIn('Active company driver',text)
        self.assertIn('audit_account_matches',text)
        self.assertIn('valid_replay_window',text)
        self.assertIn('automatic_mileage_holding_v2',text)
        self.assertIn('record_real_job',text)

    def test_no_unverified_provider_autocredit(self):
        source=(ROOT/'trucky_linked_allocation_v2.py').read_text()
        self.assertIn("s.verified_at IS NOT NULL",source)
        self.assertIn("r.status='approved'",source)
        self.assertIn('account not in member_ids and user not in alternate_member_ids',source)
