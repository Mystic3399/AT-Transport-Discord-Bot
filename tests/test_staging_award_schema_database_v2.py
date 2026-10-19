"""Disposable PostgreSQL regression for staging award uniqueness.

Run only against a database explicitly marked disposable.
"""
import os
import unittest
import asyncio
from datetime import datetime, timezone
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
            "100", "km", provider_account_id="991234567890123441",
            mileage_type="race")
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

    async def test_cross_provider_canonical_credit_is_atomic_under_concurrency(self):
        user = 991234567890123450
        at = datetime(2026, 10, 18, 22, 29, tzinfo=timezone.utc)
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO unified_driver_profiles(discord_user_id)
                VALUES($1) ON CONFLICT DO NOTHING
            """, user)
            for provider, account_id in (
                ("trucksbook", "991234567890123451"),
                ("trucky", "991234567890123452"),
            ):
                await conn.execute("""
                    INSERT INTO unified_provider_links
                        (provider,provider_account_id,discord_user_id,verified_at)
                    VALUES($1,$2,$3,NOW()) ON CONFLICT DO NOTHING
                """, provider, account_id, user)
        await install_staging_awards(self.pool)
        for provider, job_id, account_id in (
            ("trucksbook", "900000001", "991234567890123451"),
            ("trucky", "900000002", "991234567890123452"),
        ):
            await record_normalized_provider_job(
                self.pool, provider, job_id, user, "176", "mi",
                provider_account_id=account_id, delivered_at=at,
            )

        # A shared canonical key is supplied only after separate review. Both
        # provider IDs remain distinct, while the credit ledger is unique.
        async def award(provider, job_id):
            return await award_staging_miles(
                self.pool, provider, job_id, user, "176",
                mileage_type="real",
                canonical_delivery_key="v2:test-reviewed-shared-journey-001",
            )

        results = await asyncio.gather(
            award("trucksbook", "900000001"),
            award("trucky", "900000002"),
        )
        self.assertEqual(
            sorted(result["status"] for result in results),
            ["duplicate_delivery_or_provider_job", "staging_awarded"],
        )
        async with self.pool.acquire() as conn:
            credit_count = await conn.fetchval("""
                SELECT count(*) FROM unified_staging_award_jobs
                WHERE discord_user_id=$1
            """, user)
            carry = await conn.fetchrow("""
                SELECT whole_miles,fractional_miles
                FROM unified_staging_award_carry WHERE discord_user_id=$1
            """, user)
        self.assertEqual(credit_count, 1)
        self.assertEqual(carry["whole_miles"], 176)
        self.assertEqual(carry["fractional_miles"], Decimal("0"))

    async def test_failed_carry_update_rolls_back_credit_row(self):
        user = 991234567890123460
        account_id = "991234567890123461"
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO unified_driver_profiles(discord_user_id)
                VALUES($1) ON CONFLICT DO NOTHING
            """, user)
            await conn.execute("""
                INSERT INTO unified_provider_links
                    (provider,provider_account_id,discord_user_id,verified_at)
                VALUES('trucky',$1,$2,NOW()) ON CONFLICT DO NOTHING
            """, account_id, user)
        await install_staging_awards(self.pool)
        await record_normalized_provider_job(
            self.pool, "trucky", "900000003", user, "12.5", "mi",
            provider_account_id=account_id,
            delivered_at=datetime(2026, 10, 18, 22, 30, tzinfo=timezone.utc),
        )
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE OR REPLACE FUNCTION test_fail_staging_carry_update()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.discord_user_id = 991234567890123460 THEN
                        RAISE EXCEPTION 'injected staging transaction failure';
                    END IF;
                    RETURN NEW;
                END $$;
                DROP TRIGGER IF EXISTS test_fail_staging_carry_update
                    ON unified_staging_award_carry;
                CREATE TRIGGER test_fail_staging_carry_update
                    BEFORE UPDATE ON unified_staging_award_carry
                    FOR EACH ROW EXECUTE FUNCTION test_fail_staging_carry_update();
            """)
        try:
            with self.assertRaises(Exception):
                await award_staging_miles(
                    self.pool, "trucky", "900000003", user, "12.5",
                    canonical_delivery_key="v2:test-rollback-001",
                )
            async with self.pool.acquire() as conn:
                job_count = await conn.fetchval("""
                    SELECT count(*) FROM unified_staging_award_jobs
                    WHERE provider='trucky' AND provider_job_id='900000003'
                """)
                carry_count = await conn.fetchval("""
                    SELECT count(*) FROM unified_staging_award_carry
                    WHERE discord_user_id=$1
                """, user)
            self.assertEqual(job_count, 0)
            self.assertEqual(carry_count, 0)
        finally:
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    DROP TRIGGER IF EXISTS test_fail_staging_carry_update
                        ON unified_staging_award_carry;
                    DROP FUNCTION IF EXISTS test_fail_staging_carry_update();
                """)

if __name__ == "__main__":
    unittest.main()
