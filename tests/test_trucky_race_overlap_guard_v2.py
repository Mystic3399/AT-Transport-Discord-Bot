"""Prevent a Trucky Race record re-awarding one TrucksBook Real journey."""
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trucky_race_overlap_guard_v2 import possible_same_journey, race_overlap_hold

NOW=datetime(2026,11,21,12,0,tzinfo=timezone.utc)


def candidate():
    return {"discord_user_id":42,"provider":"trucky","provider_job_id":"800",
            "mileage_type":"race","miles":Decimal("160"),
            "delivered_at":NOW,
            "job_metadata":dict(game="ATS",origin="Seattle",destination="Brownsville",
                                cargo="Furniture")}


def trucksbook_row(*, destination="Brownsville", hours=0):
    return {"job_id":"100","accepted_distance":150,"evidence":dict(
        stats_type="Real",distance=150,unit="mi",
        game="ATS",origin="Seattle",destination=destination,cargo="Furniture",
        source_message_at=(NOW+timedelta(hours=hours)).isoformat())}


class FakeConn:
    def __init__(self, count, balance, old_trucky, rows=None):
        self.answers=iter((count,balance,old_trucky))
        self.rows=rows or []
    async def fetchval(self,query,*args):
        return next(self.answers)
    async def fetch(self,query,*args):
        return self.rows


class RaceOverlapTests(unittest.IsolatedAsyncioTestCase):
    async def test_trucky_only_driver_has_no_tb_overlap(self):
        self.assertIsNone(await race_overlap_hold(FakeConn(0,0,0),candidate()))

    async def test_historical_unexplained_real_balance_remains_held(self):
        self.assertEqual(await race_overlap_hold(FakeConn(0,600,0),candidate()),
                         "race_historical_balance_pending")

    async def test_unverifiable_tb_job_remains_held(self):
        rows=[dict(job_id="100",accepted_distance=150,evidence=None)]
        self.assertEqual(await race_overlap_hold(FakeConn(1,150,0,rows),candidate()),
                         "race_historical_route_coverage_pending")

    async def test_exact_same_route_and_delivery_time_remains_held_even_with_different_category(self):
        self.assertEqual(await race_overlap_hold(
            FakeConn(1,150,0,[trucksbook_row()]),candidate()),
            "race_potential_duplicate")

    async def test_other_route_is_independent_with_complete_source(self):
        self.assertIsNone(await race_overlap_hold(
            FakeConn(1,150,0,[trucksbook_row(destination="Omak")]),candidate()))

    async def test_other_day_is_independent_with_complete_source(self):
        self.assertIsNone(await race_overlap_hold(
            FakeConn(1,150,0,[trucksbook_row(hours=3)]),candidate()))

    async def test_source_without_route_can_prove_only_distant_nonduplicate(self):
        distant={"job_id":"100","accepted_distance":150,"evidence":dict(
            stats_type="Real",distance=150,unit="mi",
            source_message_at=(NOW-timedelta(hours=5)).isoformat())}
        self.assertIsNone(await race_overlap_hold(
            FakeConn(1,150,0,[distant]),candidate()))
        near={"job_id":"100","accepted_distance":150,"evidence":dict(
            stats_type="Real",distance=150,unit="mi",
            source_message_at=(NOW-timedelta(minutes=30)).isoformat())}
        self.assertEqual(await race_overlap_hold(
            FakeConn(1,150,0,[near]),candidate()),
            "race_historical_route_coverage_pending")

    async def test_source_game_does_not_match(self):
        other=dict(provider="trucksbook",discord_user_id=42,
                   delivered_at=NOW,metadata=dict(game="ETS2",origin="Seattle",
                   destination="Brownsville",cargo="Furniture"),mileage_type="real")
        self.assertFalse(possible_same_journey(candidate(),other))


if __name__=="__main__":
    unittest.main()
