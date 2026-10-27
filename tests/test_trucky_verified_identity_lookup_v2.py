import unittest
from unittest.mock import AsyncMock
from trucky_verified_identity_lookup_v2 import (
    get_verified_trucky_account_id, review_job_for_registered_driver,
)

JOB = {"company_id": 49041, "game_id": 1, "id": 123, "user_id": 456,
       "status": "completed", "completed_at": "2026-10-01T12:00:00Z",
       "stats_type": "real_miles", "distance_unit": "km",
       "driven_distance_km": 120.5}


class Connection:
    def __init__(self, rows):
        self.fetch = AsyncMock(return_value=rows)


class Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *args):
        return False


class Pool:
    def __init__(self, rows):
        self.connection = Connection(rows)

    def acquire(self):
        return Acquire(self.connection)


class VerifiedIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_verified_link_can_be_read(self):
        self.assertEqual(await get_verified_trucky_account_id(
            Pool([{"provider_account_id": "456"}]), 789), "456")

    async def test_missing_link_fails_closed(self):
        self.assertIsNone(await get_verified_trucky_account_id(Pool([]), 789))

    async def test_duplicate_rows_fail_closed(self):
        self.assertIsNone(await get_verified_trucky_account_id(
            Pool([{"provider_account_id": "456"}] * 2), 789))

    async def test_invalid_id_fails_closed(self):
        self.assertIsNone(await get_verified_trucky_account_id(
            Pool([{"provider_account_id": "abc"}]), 789))

    async def test_verified_job_remains_unawardable(self):
        candidate = await review_job_for_registered_driver(
            Pool([{"provider_account_id": "456"}]), JOB, 789)
        self.assertEqual(candidate["provider_job_id"], "123")
        self.assertFalse(candidate["eligible_for_award"])
        self.assertFalse(candidate["trusted"])

    async def test_wrong_job_owner_fails_closed(self):
        self.assertIsNone(await review_job_for_registered_driver(
            Pool([{"provider_account_id": "999"}]), JOB, 789))


if __name__ == "__main__":
    unittest.main()

