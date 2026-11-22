"""Trucky Real automatic release only when no earlier provider mileage exists."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock
import unittest
from automatic_linked_mileage_v2 import (
    safely_independent_trucky_real_without_trucksbook, reconcile_trucky,
)

DELIVERED = datetime.now(timezone.utc) - timedelta(days=2)

def job(**changes):
    return dict(discord_user_id=4242,provider_job_id='903919',
                mileage_type='real',delivered_at=DELIVERED,
                miles=Decimal('240.8'),**changes)

def facts(**changes):
    return dict(profile_real=0,trucky_real=Decimal(0),trucksbook_jobs=0,
                trucksbook_observations=0,trucksbook_claims=0,
                race_overlap=0,**changes)

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
            dict(profile_real=20,trucky_real=Decimal('20.4')),
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
        conn.fetchrow=AsyncMock(side_effect=[None,facts(trucksbook_jobs=1)])
        self.assertEqual(await reconcile_trucky(conn,job()),
                         (None,'route_evidence_pending'))
