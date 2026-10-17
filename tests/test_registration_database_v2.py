"""Database integration tests for V2 registration; uses disposable TEST_DATABASE_URL only."""
import asyncio
import hashlib
import os
import unittest

import asyncpg

import unified_driver_profiles as profiles


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "Requires disposable TEST_DATABASE_URL")
class RegistrationDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.conn = await asyncpg.connect(os.environ["TEST_DATABASE_URL"])
        # The CI database is disposable; do not run these tests on production.
        if os.getenv("AT_V2_TEST_DATABASE_DISPOSABLE") != "YES":
            self.skipTest("Explicit disposable-database opt-in required")
        await self.conn.execute(profiles.SCHEMA)
        self.pool = await asyncpg.create_pool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=2)

    async def asyncTearDown(self):
        if hasattr(self, "pool"):
            await self.pool.close()
        if hasattr(self, "conn"):
            await self.conn.close()

    async def test_normalized_job_audit_and_duplicate_rejection(self):
        user = 991234567890123460
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO unified_driver_profiles(discord_user_id) VALUES($1)
                ON CONFLICT DO NOTHING
            """, user)
            await conn.execute("""
                INSERT INTO unified_provider_links
                    (provider,provider_account_id,discord_user_id,verified_at)
                VALUES('trucky','991234560',$1,NOW())
                ON CONFLICT DO NOTHING
            """, user)
        self.assertTrue(await profiles.record_normalized_provider_job(
            self.pool, "trucky", "job-991234560", user, "100", "km"
        ))
        self.assertFalse(await profiles.record_normalized_provider_job(
            self.pool, "trucky", "job-991234560", user, "100", "km"
        ))
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT real_miles,race_miles,source_payload
                FROM unified_provider_job_events
                WHERE provider='trucky' AND provider_job_id='job-991234560'
            """)
        from decimal import Decimal
        self.assertEqual(row["real_miles"], Decimal("62.14"))
        self.assertEqual(row["race_miles"], Decimal("0"))
        self.assertEqual(row["source_payload"]["original_unit"], "km")
        self.assertEqual(row["source_payload"]["original_distance"], "100")
        with self.assertRaises(ValueError):
            await profiles.record_normalized_provider_job(
                self.pool, "trucky", "job-991234560", user, "101", "km"
            )

    async def test_cross_provider_candidate_is_read_only(self):
        from datetime import datetime, timedelta, timezone
        from unified_job_review_v2 import suggest_cross_provider_matches
        user = 991234567890123470
        now = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
        meta = {"game": "ets2", "origin": "London",
                "destination": "Duisburg", "cargo": "Machinery"}
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO unified_driver_profiles(discord_user_id) VALUES($1)
                ON CONFLICT DO NOTHING
            """, user)
            for provider, account in (("trucky", "991234570"),
                                      ("trucksbook", "991234571")):
                await conn.execute("""
                    INSERT INTO unified_provider_links
                        (provider,provider_account_id,discord_user_id,verified_at)
                    VALUES($1,$2,$3,NOW()) ON CONFLICT DO NOTHING
                """, provider, account, user)
        await profiles.record_normalized_provider_job(
            self.pool, "trucky", "match-trucky-991234570", user,
            "100", "km", delivered_at=now, job_metadata=meta)
        await profiles.record_normalized_provider_job(
            self.pool, "trucksbook", "match-trucksbook-991234571", user,
            "62.14", "mi", delivered_at=now + timedelta(minutes=10),
            job_metadata=meta)
        matches = await suggest_cross_provider_matches(
            self.pool, "trucky", "match-trucky-991234570")
        self.assertEqual(matches, [("trucksbook", "match-trucksbook-991234571")])
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval(
                "SELECT COUNT(*) FROM unified_job_matches WHERE provider_job_id LIKE 'match-%'"
            ), 0)

    async def test_steam_replay_account_switch_and_trucky_gate(self):
        user = 991234567890123450
        other = 991234567890123451
        steam = "76561198000000000"
        second_steam = "76561198000000001"
        await profiles.begin_registration(self.pool, user, 991234567890123452)
        await profiles.begin_registration(self.pool, other, 991234567890123453)
        await profiles.submit_provider_claim(self.pool, user, "trucky", "209123", "Tester")

        async def link(who, identity, state, nonce):
            digest = hashlib.sha256(state.encode("ascii")).hexdigest()
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO unified_steam_auth_states
                        (state_hash,discord_user_id,expected_return_to,expires_at)
                    VALUES($1,$2,'https://example.test/auth/steam/callback',NOW()+INTERVAL '10 minutes')
                """, digest, who)
            await profiles.bind_verified_steam(self.pool, who, identity, digest, nonce)

        await link(user, steam, "state-first", "nonce-first")
        with self.assertRaises(ValueError):
            await profiles.bind_verified_steam(
                self.pool, user, steam,
                hashlib.sha256(b"state-first").hexdigest(), "nonce-first"
            )
        with self.assertRaises(ValueError):
            await link(user, second_steam, "state-second", "nonce-second")
        with self.assertRaises(ValueError):
            await link(other, steam, "state-third", "nonce-third")
        with self.assertRaises(ValueError):
            await profiles.auto_approve_trucky_if_steam_matches(
                self.pool, user, "209123", steam,
                trusted_provider_source=False, membership_confirmed=True
            )
        with self.assertRaises(ValueError):
            await profiles.auto_approve_trucky_if_steam_matches(
                self.pool, user, "209123", second_steam,
                trusted_provider_source=True, membership_confirmed=True
            )
        async with self.pool.acquire() as conn:
            status = await conn.fetchval(
                "SELECT status FROM unified_driver_profiles WHERE discord_user_id=$1", user
            )
            self.assertEqual(status, "pending")
        approved = await profiles.auto_approve_trucky_if_steam_matches(
            self.pool, user, "209123", steam,
            trusted_provider_source=True, membership_confirmed=True
        )
        self.assertEqual(approved, "209123")
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval(
                "SELECT status FROM unified_driver_profiles WHERE discord_user_id=$1", user
            ), "active")
            self.assertEqual(await conn.fetchval(
                "SELECT status FROM unified_provider_claims WHERE discord_user_id=$1 AND provider='trucky'",
                user
            ), "approved")


if __name__ == "__main__":
    unittest.main()
