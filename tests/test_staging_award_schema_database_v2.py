"""Disposable PostgreSQL regression for staging award uniqueness.

Run only against a database explicitly marked disposable.
"""
import os
import unittest
from decimal import Decimal

try:
    import asyncpg
except ImportError:
    asyncpg = None

from unified_driver_profiles import SCHEMA as PROFILE_SCHEMA
from unified_staging_atomic_awards_v2 import install_staging_awards, award_staging_miles
from unified_driver_profiles import record_normalized_provider_job

@unittest.skipUnless(
    asyncpg is not None
    and os.getenv("AT_V2_TEST_DATABASE_DISPOSABLE") == "YES"
    and "/v2_test" in os.getenv("TEST_DATABASE_URL", ""),
    "Requires disposable v2_test PostgreSQL",
)
class StagingSchemaDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = await asyncpg.create_pool(os.environ["TEST_DATABASE_URL"])
        async with self.pool.acquire() as conn:
            await conn.execute(PROFILE_SCHEMA)

    async def asyncTearDown(self):
        await self.pool.close()

    async def test_schema_installs_twice_with_canonical_uniqueness(self):
        await install_staging_awards(self.pool)
        await install_staging_awards(self.pool)
        async with self.pool.acquire() as conn:
            found = await conn.fetchval("""
                SELECT count(*) FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                WHERE t.relname='unified_staging_award_jobs'
                  AND c.contype='u'
            """)
        self.assertGreaterEqual(found, 1)

    async def test_trucky_race_awards_use_independent_postgres_carry(self):
        user = 991234567890123440
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO unified_driver_profiles(discord_user_id)
                VALUES($1) ON CONFLICT DO NOTHING
            """, user)
            await conn.execute("""
                INSERT INTO unified_provider_links
                    (provider,provider_account_id,discord_user_id,verified_at)
                VALUES('trucky','991234567890123441',$1,NOW())
                ON CONFLICT DO NOTHING
            """, user)
        await install_staging_awards(self.pool)
        await record_normalized_provider_job(
            self.pool, "trucky", "race-991234567890123441", user,
            "100", "km", mileage_type="race")
        result = await award_staging_miles(
            self.pool, "trucky", "race-991234567890123441", user, "62.14",
            mileage_type="race",
            canonical_delivery_key="provider:trucky:race-991234567890123441")
        self.assertEqual(result["credited_race_miles"], 62)
        self.assertEqual(Decimal(result["fractional_miles"]), Decimal("0.14"))
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT whole_miles,fractional_miles,race_whole_miles,
                       race_fractional_miles
                FROM unified_staging_award_carry WHERE discord_user_id=$1
            """, user)
        self.assertEqual(row["whole_miles"], 0)
        self.assertEqual(row["fractional_miles"], Decimal("0"))
        self.assertEqual(row["race_whole_miles"], 62)
        self.assertEqual(row["race_fractional_miles"], Decimal("0.14"))
        duplicate = await award_staging_miles(
            self.pool, "trucky", "race-991234567890123441", user, "62.14",
            mileage_type="race",
            canonical_delivery_key="provider:trucky:race-991234567890123441")
        self.assertEqual(duplicate["status"], "duplicate_provider_job")

if __name__ == "__main__":
    unittest.main()
