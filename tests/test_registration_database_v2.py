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
