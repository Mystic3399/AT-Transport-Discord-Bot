import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import unittest

from historical_route_coverage_audit_v2 import inspect_historical_route_coverage


def evidence(*, destination="Lille"):
    return dict(game="ETS2", origin="Paris", destination=destination, cargo="Food",
                stats_type="Real", unit="mi", distance=100,
                source_message_at="2026-11-01T12:00:00+00:00")


class CoverageAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_audit_reports_missing_and_conflicting_without_writes(self):
        conn = MagicMock()
        conn.fetch = AsyncMock(return_value=[
            dict(job_id="1", accepted_distance=100, evidence=evidence()),
            dict(job_id="2", accepted_distance=100, evidence=None),
            dict(job_id="3", accepted_distance=100, evidence=evidence()),
            dict(job_id="3", accepted_distance=100, evidence=evidence(destination="Berlin")),
            dict(job_id="4", accepted_distance=100, evidence={"invalid": True}),
        ])
        result = await inspect_historical_route_coverage(conn, 789)
        self.assertEqual(result, {
            "processed_real_jobs": 4,
            "comparable_verified_jobs": 1,
            "missing_verified_webhook": 1,
            "invalid_or_incomplete_evidence": 1,
            "conflicting_verified_versions": 1,
            "mileage_changed": False,
        })
        conn.transaction.assert_called_once_with(readonly=True, isolation="repeatable_read")
        conn.execute.assert_not_called()

    async def test_empty_history_does_not_imply_credit_permission(self):
        conn = MagicMock()
        conn.fetch = AsyncMock(return_value=[])
        result = await inspect_historical_route_coverage(conn, 789)
        self.assertEqual(result["processed_real_jobs"], 0)
        self.assertFalse(result["mileage_changed"])
        conn.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
