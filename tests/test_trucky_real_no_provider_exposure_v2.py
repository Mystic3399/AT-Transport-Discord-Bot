"""Trucky Real automatic release only when no earlier provider mileage exists."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock
import unittest
from automatic_linked_mileage_v2 import (
    safely_independent_trucky_real_without_trucksbook,
    safely_independent_trucky_real_after_trucksbook_processing, reconcile_trucky,
)

DELIVERED = datetime.now(timezone.utc) - timedelta(days=2)

def job(**changes):
    return dict(discord_user_id=4242,provider_job_id='903919',
                mileage_type='real',delivered_at=DELIVERED,
                miles=Decimal('240.8'),**changes)

def facts(**changes):
    values=dict(profile_real=0,trucky_real=Decimal(0),trucksbook_jobs=0,
                trucksbook_observations=0,trucksbook_claims=0,race_overlap=0)
    values.update(changes)
    return values

class AutomaticNoTrucksBookExposure(unittest.IsolatedAsyncioTestCase):
    async def test_real_job_missing_route_can_be_credited_only_with_zero_prior_exposure(self):
        conn=type('FakeConn',(),{})()
        conn.fetchrow=AsyncMock(side_effect=[None,facts()])
        self.assertEqual(await reconcile_trucky(conn,job()),(Decimal(0),None))
        self.assertEqual(conn.fetchrow.await_count,2)
        self.assertIn("LOWER(statistics) LIKE 'real%'",
                      conn.fetchrow.await_args.args[0])

    async def test_old_unexplained_balances_stay_held(self):
        variants=[
            dict(profile_real=500,trucky_real=Decimal(0)),
            dict(trucksbook_jobs=1),
            dict(trucksbook_observations=1),
            dict(trucksbook_claims=1),
            dict(race_overlap=1),
            dict(profile_real=21,trucky_real=Decimal('20.4')),
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                conn=type('Conn',(),{})()
                conn.fetchrow=AsyncMock(return_value=facts(**variant))
                self.assertFalse(await safely_independent_trucky_real_without_trucksbook(
                    conn,job()))

    async def test_exact_cumulative_trucky_balance_is_allowed(self):
        conn=type('Conn',(),{})()
        conn.fetchrow=AsyncMock(return_value=facts(profile_real=20,
                                                  trucky_real=Decimal('20.8')))
        self.assertTrue(await safely_independent_trucky_real_without_trucksbook(
            conn,job()))

    async def test_recent_delivery_and_race_are_not_fast_tracked(self):
        conn=type('Conn',(),{})()
        conn.fetchrow=AsyncMock(return_value=facts())
        fresh=job()
        fresh['delivered_at']=datetime.now(timezone.utc)-timedelta(minutes=10)
        self.assertFalse(await safely_independent_trucky_real_without_trucksbook(
            conn,fresh))
        race=job()
        race['mileage_type']='race'
        self.assertFalse(await safely_independent_trucky_real_without_trucksbook(
            conn,race))
        conn.fetchrow.assert_not_awaited()

    async def test_no_route_and_nonzero_trucksbook_never_credits(self):
        conn=type('Conn',(),{})()
        conn.fetchrow=AsyncMock(side_effect=[None,facts(trucksbook_jobs=1),None])
        self.assertEqual(await reconcile_trucky(conn,job()),
                         (None,'route_evidence_pending'))


class AutomaticAfterProcessedTrucksBookTests(unittest.IsolatedAsyncioTestCase):
    def evidence(self, **changes):
        values = dict(
            profile_real=380, tb_real=350, trucky_real=Decimal("30.8"),
            tb_count=3,last_tb_processed=DELIVERED-timedelta(days=4),
            unpaid_tb_observations=0,close_race_credits=0,
        )
        values.update(changes)
        return values

    async def check(self, values, accepted):
        conn=type('Conn',(),{})()
        conn.fetchrow=AsyncMock(return_value=values)
        self.assertIs(
            await safely_independent_trucky_real_after_trucksbook_processing(
                conn,job()), accepted)
        conn.fetchrow.assert_awaited_once()

    async def test_explained_prior_real_all_older_and_no_new_tb_sources(self):
        await self.check(self.evidence(),True)

    async def test_unknown_prior_real_balance_remains_held(self):
        await self.check(self.evidence(profile_real=900),False)

    async def test_older_trucksbook_processing_is_required(self):
        await self.check(self.evidence(last_tb_processed=None),False)
        await self.check(self.evidence(last_tb_processed=DELIVERED-timedelta(minutes=35)),False)
        await self.check(self.evidence(last_tb_processed=DELIVERED+timedelta(days=1)),False)

    async def test_unprocessed_trucksbook_and_race_sources_block_credit(self):
        await self.check(self.evidence(unpaid_tb_observations=1),False)
        await self.check(self.evidence(close_race_credits=1),False)

    async def test_no_processed_tb_history_is_not_covered_by_new_rule(self):
        await self.check(self.evidence(tb_count=0),False)

    async def test_automatic_reconciliation_can_release_an_independent_job(self):
        conn=type('Conn',(),{})()
        conn.fetchrow=AsyncMock(side_effect=[
            None,facts(trucksbook_jobs=3),self.evidence()])
        self.assertEqual(await reconcile_trucky(conn,job()),
                         (Decimal(0),None))
        self.assertEqual(conn.fetchrow.await_count,3)

    async def test_race_not_eligible_for_real_automatic_release(self):
        conn=type('Conn',(),{})()
        conn.fetchrow=AsyncMock()
        race=job()
        race["mileage_type"]="race"
        self.assertFalse(
            await safely_independent_trucky_real_after_trucksbook_processing(conn,race))
        conn.fetchrow.assert_not_awaited()
