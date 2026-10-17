import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from unified_staging_atomic_awards_v2 import award_staging_real_miles


class _Connection:
    def __init__(self):
        self.whole = 0
        self.fraction = Decimal("0")
        self.inserted = set()
        self.canonical = set()
        self.saved = {}
        self.current = None

    def transaction(self):
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=None)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    async def fetchrow(self, query, *args):
        if "FROM unified_provider_job_events" in query:
            return {"discord_user_id":123, "real_miles":Decimal(str(self.current)),
                    "race_miles":Decimal("0")}
        if "FROM unified_staging_award_jobs" in query:
            return self.saved.get((args[0], args[1]))
        if "FROM unified_staging_award_carry" in query:
            return {"whole_miles":self.whole,"fractional_miles":self.fraction}
        raise AssertionError(query)

    async def fetchval(self, query, *args):
        key = (args[0], args[1])
        if key in self.inserted or (args[2], args[4]) in self.canonical:
            return None
        self.inserted.add(key)
        self.canonical.add((args[2], args[4]))
        self.saved[key] = {"discord_user_id":args[2], "miles":args[3],
                           "canonical_delivery_key":args[4]}
        return args[1]

    async def execute(self, query, *args):
        if "UPDATE unified_staging_award_carry" in query:
            self.whole += args[1]
            self.fraction = args[2]
        return "OK"


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=self.conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm


class StagingAwardTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_provider_id_and_fractional_carry(self):
        conn = _Connection()
        pool = _Pool(conn)
        conn.current = "0.65"
        first = await award_staging_real_miles(pool, "trucky", "job-a", 123, "0.65", canonical_delivery_key="delivery-a")
        self.assertEqual(first["credited_whole_miles"], 0)
        conn.current = "0.55"
        second = await award_staging_real_miles(pool, "trucky", "job-b", 123, "0.55", canonical_delivery_key="delivery-b")
        self.assertEqual(second["credited_whole_miles"], 1)
        self.assertEqual(second["fractional_miles"], "0.20")
        conn.current = "0.65"
        duplicate = await award_staging_real_miles(pool, "trucky", "job-a", 123, "0.65", canonical_delivery_key="delivery-a")
        self.assertEqual(duplicate["status"], "duplicate_provider_job")
        self.assertEqual(conn.whole, 1)

    async def test_conflicting_replay_is_rejected(self):
        conn = _Connection()
        conn.current = "5"
        pool = _Pool(conn)
        await award_staging_real_miles(pool, "trucky", "job-a", 123, "5", canonical_delivery_key="journey-1")
        with self.assertRaises(ValueError):
            await award_staging_real_miles(pool, "trucky", "job-a", 123, "5", canonical_delivery_key="different-journey")
        self.assertEqual(conn.whole, 5)

    async def test_other_provider_same_delivery_cannot_credit_twice(self):
        conn = _Connection()
        conn.current = "5"
        pool = _Pool(conn)
        await award_staging_real_miles(pool, "trucky", "job-a", 123, "5", canonical_delivery_key="journey-1")
        second = await award_staging_real_miles(pool, "trucksbook", "job-b", 123, "5", canonical_delivery_key="journey-1")
        self.assertEqual(second["status"], "duplicate_delivery_or_provider_job")
        self.assertEqual(conn.whole, 5)

    async def test_excessive_precision_rejected_before_database_write(self):
        conn = _Connection()
        conn.current = "1.12345678901"
        with self.assertRaises(ValueError):
            await award_staging_real_miles(_Pool(conn), "trucky", "job-a", 123,
                "1.12345678901", canonical_delivery_key="journey-1")
        self.assertEqual(conn.whole, 0)

    async def test_invalid_mileage_rejected_before_database_write(self):
        for amount in ("NaN", "Infinity", "-1", "0", "not-a-number", None):
            with self.subTest(amount=amount):
                conn = _Connection()
                with self.assertRaises(ValueError):
                    await award_staging_real_miles(_Pool(conn), "trucky", "job-a",
                        123, amount, canonical_delivery_key="journey-1")
                self.assertEqual(conn.whole, 0)

    async def test_driver_mismatch_rejected(self):
        conn = _Connection()
        conn.current = "5"
        with self.assertRaises(ValueError):
            await award_staging_real_miles(_Pool(conn), "trucky", "job-a", 999, "5", canonical_delivery_key="delivery-a")


if __name__ == "__main__":
    unittest.main()
