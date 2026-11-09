import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from driver_statistics_consistency_audit_v2 import inspect_driver_statistics


class StatisticsConsistencyAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_detects_mismatch_without_writing(self):
        conn = MagicMock()
        conn.fetch = AsyncMock(return_value=[
            dict(discord_user_id=1, legacy_real=130, baseline_real=100,
                 v2_real=30, race_miles=50, hall_real=100, hall_tier="Veteran"),
            dict(discord_user_id=2, legacy_real=120, baseline_real=100,
                 v2_real=30, race_miles=0, hall_real=140, hall_tier="Veteran"),
            dict(discord_user_id=3, legacy_real=0, baseline_real=None,
                 v2_real=0, race_miles=0, hall_real=None, hall_tier=None),
            dict(discord_user_id=4, legacy_real=101, baseline_real=100,
                 v2_real=Decimal("1.75"), race_miles=0, hall_real=None, hall_tier=None),
            dict(discord_user_id=5, legacy_real=None, baseline_real=0,
                 v2_real=Decimal("5"), race_miles=0, hall_real=None, hall_tier=None),
        ])
        rows = await inspect_driver_statistics(conn)
        self.assertEqual(rows[0]["issues"], [])
        self.assertEqual(rows[1]["issues"], [
            "legacy_vs_unified_real_mismatch",
            "hall_record_exceeds_current_real",
        ])
        self.assertEqual(rows[2]["issues"], ["baseline_missing"])
        self.assertEqual(rows[3]["expected_real"], 101)
        self.assertEqual(rows[3]["issues"], [])
        self.assertEqual(rows[4]["issues"], ["legacy_driver_progress_missing"])
        self.assertIsNone(rows[4]["legacy_real"])
        conn.transaction.assert_called_once_with(
            readonly=True, isolation="repeatable_read")
        conn.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
