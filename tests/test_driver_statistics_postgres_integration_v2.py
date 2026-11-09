"""Disposable PostgreSQL integration test; never point at a production database."""
import asyncio
import os
import unittest
from decimal import Decimal

from driver_statistics_consistency_audit_v2 import (
    inspect_driver_statistics, summarize_driver_statistics, audit_exit_code,
)


class DisposableStatisticsAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_sql_readonly_audit(self):
        if os.getenv("AT_V2_TEST_DATABASE_DISPOSABLE") != "YES":
            self.skipTest("Disposable database not explicitly enabled")
        url = os.environ.get("TEST_DATABASE_URL")
        if not url or "127.0.0.1" not in url:
            self.skipTest("Requires local disposable PostgreSQL")
        import asyncpg
        conn = await asyncpg.connect(url)
        try:
            # Create isolated tables in the disposable DB only.
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS driver_progress (
                    discord_user_id BIGINT PRIMARY KEY,
                    real_miles BIGINT
                );
                CREATE TABLE IF NOT EXISTS unified_v2_production_mileage_baselines (
                    discord_user_id BIGINT PRIMARY KEY,
                    trucksbook_real_miles BIGINT
                );
                CREATE TABLE IF NOT EXISTS unified_v2_production_mileage_totals (
                    discord_user_id BIGINT PRIMARY KEY,
                    combined_real_miles NUMERIC(24,10),
                    trucky_race_miles NUMERIC(24,10)
                );
                CREATE TABLE IF NOT EXISTS hall_of_fame (
                    discord_user_id BIGINT PRIMARY KEY,
                    highest_verified_real_miles BIGINT,
                    highest_tier TEXT
                );
            """)
            await conn.execute("""
                INSERT INTO driver_progress VALUES (910000001, 101)
                    ON CONFLICT (discord_user_id) DO UPDATE SET real_miles=101;
                INSERT INTO unified_v2_production_mileage_baselines
                    VALUES (910000001,100), (910000002,0)
                    ON CONFLICT DO NOTHING;
                INSERT INTO unified_v2_production_mileage_totals
                    VALUES (910000001,1.75,0), (910000002,5,10)
                    ON CONFLICT DO NOTHING;
            """)
            result = await inspect_driver_statistics(conn)
            summary = summarize_driver_statistics(result)
            self.assertEqual(result["status"], "audited")
            self.assertEqual(summary["issue_counts"]["legacy_driver_progress_missing"], 1)
            self.assertEqual(audit_exit_code(summary), 1)
            self.assertEqual(next(r for r in result["drivers"]
                                  if r["discord_user_id"] == 910000001)["expected_real"], 101)
        finally:
            await conn.close()


if __name__ == "__main__":
    unittest.main()
