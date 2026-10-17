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
from unified_staging_atomic_awards_v2 import install_staging_awards

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

if __name__ == "__main__":
    unittest.main()
