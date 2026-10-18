"""Database integration tests for V2 registration; uses disposable TEST_DATABASE_URL only."""
import asyncio
import hashlib
import json
import os
import unittest
from datetime import datetime, timezone

import asyncpg

import unified_driver_profiles as profiles


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "Requires disposable TEST_DATABASE_URL")
class RegistrationDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Refuse to connect before explicit opt-in and strict local disposable DB validation.
        if os.getenv("AT_V2_TEST_DATABASE_DISPOSABLE") != "YES":
            self.skipTest("Explicit disposable-database opt-in required")
        from urllib.parse import urlsplit
        db_url = urlsplit(os.environ["TEST_DATABASE_URL"])
        if (db_url.hostname not in ("localhost", "127.0.0.1", "::1")
                or db_url.path != "/v2_test"
                or db_url.scheme not in ("postgres", "postgresql")):
            self.skipTest("Only localhost/v2_test disposable PostgreSQL is permitted")
        self.conn = await asyncpg.connect(os.environ["TEST_DATABASE_URL"])
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
        self.assertEqual(json.loads(row["source_payload"])["original_unit"], "km")
        self.assertEqual(json.loads(row["source_payload"])["original_distance"], "100")
        with self.assertRaises(ValueError):
            await profiles.record_normalized_provider_job(
                self.pool, "trucky", "job-991234560", user, "101", "km"
            )

    async def test_signed_trucky_receipt_stores_no_identity_or_distance(self):
        digest = hashlib.sha256(b"signed event bytes").hexdigest()
        receipt_id = await profiles.record_trucky_webhook_receipt(
            self.pool, digest, "job_completed", 991234560
        )
        self.assertIsNotNone(receipt_id)
        duplicate_receipt_id = await profiles.record_trucky_webhook_receipt(
            self.pool, digest, "job_completed", 991234560
        )
        self.assertEqual(duplicate_receipt_id, receipt_id)
        async with self.pool.acquire() as conn:
            receipt = await conn.fetchrow("""
                SELECT payload_sha256,event_type,candidate_job_id,review_status
                FROM unified_trucky_webhook_receipts WHERE receipt_id=$1
            """, receipt_id)
            awards = await conn.fetchval(
                """SELECT COUNT(*) FROM unified_provider_job_events
                   WHERE provider='trucky' AND provider_job_id='991234560'"""
            )
        self.assertEqual(receipt["payload_sha256"], digest)
        self.assertEqual(receipt["candidate_job_id"], "991234560")
        self.assertEqual(receipt["review_status"], "needs_api_verification")
        self.assertEqual(awards, 0)

    async def test_receipt_audit_returns_only_bounded_safe_fields(self):
        digest = hashlib.sha256(b"audit receipt bytes").hexdigest()
        await profiles.record_trucky_webhook_receipt(
            self.pool, digest, "job_completed", "991234561"
        )
        rows = await profiles.list_trucky_receipt_audit(self.pool, "991234561")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_type"], "job_completed")
        self.assertEqual(rows[0]["review_status"], "needs_api_verification")
        self.assertTrue(rows[0]["received_at"].tzinfo is not None)
        self.assertNotIn("payload_sha256", rows[0])
        self.assertNotIn("source_payload", rows[0])
        with self.assertRaises(ValueError):
            await profiles.list_trucky_receipt_audit(self.pool, "bad-id")

    async def test_authenticated_trucky_account_id_requires_verified_steam_link_chain(self):
        user = 991234567890123466
        account = "991234566"
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO unified_driver_profiles(discord_user_id) VALUES($1)
            """, user)
            await conn.execute("""
                INSERT INTO unified_steam_links(discord_user_id,steam_id64,verified_at)
                VALUES($1,'76561198012345678',NOW())
            """, user)
            await conn.execute("""
                INSERT INTO unified_provider_claims
                    (discord_user_id,provider,provider_account_id,
                     membership_confirmed,ownership_confirmed,status)
                VALUES($1,'trucky',$2,TRUE,TRUE,'approved')
            """, user, account)
            await conn.execute("""
                INSERT INTO unified_provider_links
                    (provider,provider_account_id,discord_user_id,verified_at)
                VALUES('trucky',$2,$1,NOW())
            """, user, account)

        self.assertEqual(
            (await profiles.verify_trucky_account_against_verified_links(
                self.pool, account))["status"],
            "verified_trucky_account_link_matches_steam",
        )
        self.assertEqual(
            (await profiles.verify_trucky_account_against_verified_links(
                self.pool, "991234567"))["status"],
            "claim_missing",
        )

    async def test_staging_trucky_receipt_review_transition_is_gated_and_idempotent(self):
        from unittest.mock import patch
        digest = hashlib.sha256(b"verified staging receipt review").hexdigest()
        await profiles.record_trucky_webhook_receipt(
            self.pool, digest, "job_completed", "9912345678"
        )
        with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "YES"}):
            blocked = await profiles.mark_staging_trucky_receipts_verified_after_api_audit(
                self.pool, "9912345678", hmac_receipt_verified=True,
                authenticated_api_job_verified=True,
                driver_identity_verified=False, active_member=True,
            )
            self.assertEqual(blocked["status"], "evidence_incomplete")
            pending = await profiles.list_trucky_receipt_audit(self.pool, "9912345678")
            self.assertEqual(pending[0]["review_status"], "needs_api_verification")

            verified = await profiles.mark_staging_trucky_receipts_verified_after_api_audit(
                self.pool, "9912345678", hmac_receipt_verified=True,
                authenticated_api_job_verified=True,
                driver_identity_verified=True, active_member=True,
            )
            self.assertEqual(verified, {"status": "verified", "updated_count": 1})
            replay = await profiles.mark_staging_trucky_receipts_verified_after_api_audit(
                self.pool, "9912345678", hmac_receipt_verified=True,
                authenticated_api_job_verified=True,
                driver_identity_verified=True, active_member=True,
            )
            self.assertEqual(replay, {"status": "already_verified", "updated_count": 0})

        async with self.pool.acquire() as conn:
            receipt = await conn.fetchrow("""
                SELECT payload_sha256,event_type,candidate_job_id,review_status
                FROM unified_trucky_webhook_receipts WHERE payload_sha256=$1
            """, digest)
            job_count = await conn.fetchval("SELECT COUNT(*) FROM unified_provider_job_events")
        self.assertEqual(receipt["payload_sha256"], digest)
        self.assertEqual(receipt["candidate_job_id"], "9912345678")
        self.assertEqual(receipt["review_status"], "verified")
        self.assertEqual(job_count, 0)

    async def test_staging_trucky_receipt_review_is_disabled_outside_staging(self):
        from unittest.mock import patch
        with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "NO"}):
            with self.assertRaises(RuntimeError):
                await profiles.mark_staging_trucky_receipts_verified_after_api_audit(
                    self.pool, "9912345678", hmac_receipt_verified=True,
                    authenticated_api_job_verified=True,
                    driver_identity_verified=True, active_member=True,
                )

    async def test_repeated_job_with_changed_delivery_time_is_rejected(self):
        from datetime import datetime, timedelta, timezone
        user = 991234567890123490
        delivered = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO unified_driver_profiles(discord_user_id)
                VALUES($1) ON CONFLICT DO NOTHING
            """, user)
            await conn.execute("""
                INSERT INTO unified_provider_links
                    (provider,provider_account_id,discord_user_id,verified_at)
                VALUES('trucky','991234590',$1,NOW()) ON CONFLICT DO NOTHING
            """, user)
        args = (self.pool, "trucky", "time-991234590", user, "100", "km")
        self.assertTrue(await profiles.record_normalized_provider_job(
            *args, delivered_at=delivered))
        self.assertFalse(await profiles.record_normalized_provider_job(
            *args, delivered_at=delivered))
        with self.assertRaises(ValueError):
            await profiles.record_normalized_provider_job(
                *args, delivered_at=delivered + timedelta(minutes=10))

    async def test_staging_ledger_isolated_and_immutable(self):
        from decimal import Decimal
        from unified_staging_ledger_v2 import install_staging_ledger, stage_decision, staging_totals
        user = 991234567890123480
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO unified_driver_profiles(discord_user_id)
                VALUES($1) ON CONFLICT DO NOTHING
            """, user)
            await conn.execute("""
                INSERT INTO unified_provider_links
                (provider,provider_account_id,discord_user_id,verified_at)
                VALUES('trucksbook','991234580',$1,NOW()) ON CONFLICT DO NOTHING
            """, user)
        await install_staging_ledger(self.pool)
        await profiles.record_normalized_provider_job(
            self.pool, "trucksbook", "ledger-991234580", user, "100", "km")
        self.assertTrue(await stage_decision(
            self.pool, "trucksbook", "ledger-991234580", "credited", 999))
        self.assertFalse(await stage_decision(
            self.pool, "trucksbook", "ledger-991234580", "credited", 999))
        self.assertEqual((await staging_totals(self.pool, user))["real_miles"], Decimal("62.14"))
        with self.assertRaises(ValueError):
            await stage_decision(self.pool, "trucksbook", "ledger-991234580", "duplicate", 999)

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

    async def test_pending_claim_has_no_deadline_and_late_signed_job_links(self):
        from datetime import timedelta
        user = 991234567890123550
        steam = "76561198000000550"
        account = "991234550"
        job_id = "991234551"
        await profiles.begin_registration(self.pool, user, 991234567890123552)
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO unified_steam_links(discord_user_id,steam_id64,verified_at)
                VALUES($1,$2,NOW()) ON CONFLICT(discord_user_id) DO UPDATE
                SET steam_id64=EXCLUDED.steam_id64,verified_at=NOW()
            """, user, steam)
        await profiles.submit_provider_claim(self.pool, user, "trucky", account, "Late Tester")
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE unified_provider_claims
                SET submitted_at=NOW()-INTERVAL '3 days'
                WHERE discord_user_id=$1 AND provider='trucky'
            """, user)
            claim = await conn.fetchrow("""
                SELECT expires_at,status FROM unified_provider_claims
                WHERE discord_user_id=$1 AND provider='trucky'
            """, user)
        self.assertIsNone(claim["expires_at"])
        self.assertEqual(claim["status"], "pending")
        digest = hashlib.sha256(b"late-signed-job-991234551").hexdigest()
        await profiles.record_trucky_webhook_receipt(
            self.pool, digest, "job_completed", job_id)
        linked = await profiles.auto_approve_trucky_if_steam_matches(
            self.pool, user, account, steam,
            trusted_provider_source=True, membership_confirmed=True,
            provider_job_id=job_id, payload_sha256=digest,
            completed_at=datetime.now(timezone.utc),
        )
        self.assertEqual(linked, user)

    async def test_trucksbook_claim_does_not_expire(self):
        user = 991234567890123560
        await profiles.begin_registration(self.pool, user, 991234567890123562)
        await profiles.submit_provider_claim(self.pool, user, "trucksbook", "991234560")
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT expires_at,status FROM unified_provider_claims
                WHERE discord_user_id=$1 AND provider='trucksbook'
            """, user)
        self.assertIsNone(row["expires_at"])
        self.assertEqual(row["status"], "pending")

    async def test_steam_replay_account_switch_and_trucky_gate(self):
        user = 991234567890123450
        other = 991234567890123451
        steam = "76561198000000000"
        second_steam = "76561198000000001"
        await profiles.begin_registration(self.pool, user, 991234567890123452)
        await profiles.begin_registration(self.pool, other, 991234567890123453)
        with self.assertRaisesRegex(ValueError, "signed job event"):
            await profiles.approve_provider_claim(
                self.pool, user, "trucky", 991234567890123499,
                membership_confirmed=True, ownership_confirmed=True,
            )

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
        await profiles.submit_provider_claim(self.pool, user, "trucky", "209123", "Tester")
        with self.assertRaises(ValueError):
            await profiles.bind_verified_steam(
                self.pool, user, steam,
                hashlib.sha256(b"state-first").hexdigest(), "nonce-first"
            )
        with self.assertRaises(ValueError):
            await link(user, second_steam, "state-second", "nonce-second")
        with self.assertRaises(ValueError):
            await link(other, steam, "state-third", "nonce-third")
        receipt = hashlib.sha256(b"signed-trucky-job-receipt").hexdigest()
        job_id = "987654321"
        await profiles.record_trucky_webhook_receipt(
            self.pool, receipt, "job_completed", job_id
        )
        with self.assertRaises(ValueError):
            await profiles.auto_approve_trucky_if_steam_matches(
                self.pool, user, "209123", steam,
                trusted_provider_source=False, membership_confirmed=True,
                provider_job_id=job_id, payload_sha256=receipt,
                completed_at=datetime.now(timezone.utc),
            )
        with self.assertRaises(profiles.TruckyIdentityVerificationError) as mismatch:
            await profiles.auto_approve_trucky_if_steam_matches(
                self.pool, user, "209123", second_steam,
                trusted_provider_source=True, membership_confirmed=True,
                provider_job_id=job_id, payload_sha256=receipt,
                completed_at=datetime.now(timezone.utc),
            )
        self.assertEqual(mismatch.exception.code, "steam_identity_mismatch")
        async with self.pool.acquire() as conn:
            status = await conn.fetchval(
                "SELECT status FROM unified_driver_profiles WHERE discord_user_id=$1", user
            )
            self.assertEqual(status, "pending")
        approved = await profiles.auto_approve_trucky_if_steam_matches(
            self.pool, user, "209123", steam,
            trusted_provider_source=True, membership_confirmed=True,
            provider_job_id=job_id, payload_sha256=receipt,
            completed_at=datetime.now(timezone.utc),
        )
        self.assertEqual(approved, user)
        replayed = await profiles.auto_approve_trucky_if_steam_matches(
            self.pool, None, "209123", steam,
            trusted_provider_source=True, membership_confirmed=True,
            provider_job_id=job_id, payload_sha256=receipt,
            completed_at=datetime.now(timezone.utc),
        )
        self.assertEqual(replayed, user)
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval(
                "SELECT status FROM unified_driver_profiles WHERE discord_user_id=$1", user
            ), "active")
            self.assertEqual(await conn.fetchval(
                "SELECT status FROM unified_provider_claims WHERE discord_user_id=$1 AND provider='trucky'",
                user
            ), "approved")
            self.assertEqual(await conn.fetchval(
                "SELECT COUNT(*) FROM unified_provider_links WHERE provider='trucky' AND provider_account_id='209123'"
            ), 1)

        await link(other, second_steam, "state-expired", "nonce-expired")
        await profiles.submit_provider_claim(self.pool, other, "trucky", "209124", "Tester Two")
        async with self.pool.acquire() as conn:
            await conn.execute("""UPDATE unified_provider_claims
                SET submitted_at=NOW()-INTERVAL '1 hour',
                    expires_at=NOW()-INTERVAL '30 minutes'
                WHERE discord_user_id=$1 AND provider='trucky'""", other)
        expired_receipt = hashlib.sha256(b"expired-trucky-receipt").hexdigest()
        await profiles.record_trucky_webhook_receipt(
            self.pool, expired_receipt, "job_completed", "987654322"
        )
        linked_user = await profiles.auto_approve_trucky_if_steam_matches(
            self.pool, None, "209124", second_steam,
            trusted_provider_source=True, membership_confirmed=True,
            provider_job_id="987654322", payload_sha256=expired_receipt,
            completed_at=datetime.now(timezone.utc),
        )
        self.assertEqual(linked_user, other)

    async def test_trucky_link_requires_verified_steam_before_comparing(self):
        user = 991234567890123470
        await profiles.begin_registration(self.pool, user, 991234567890123471)
        # Seed a claim directly to exercise the fail-closed invariant for an
        # inconsistent record that could not be created through the API.
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO unified_provider_claims
                    (discord_user_id,provider,provider_account_id,provider_display_name)
                VALUES($1,'trucky','209125','Tester Three')
            """, user)
        digest = hashlib.sha256(b"trucky-job-no-steam-link").hexdigest()
        await profiles.record_trucky_webhook_receipt(self.pool, digest, "job_completed", "987654330")
        with self.assertRaises(profiles.TruckyIdentityVerificationError) as missing:
            await profiles.auto_approve_trucky_if_steam_matches(
                self.pool, user, "209125", "76561198000000000",
                trusted_provider_source=True, membership_confirmed=True,
                provider_job_id="987654330", payload_sha256=digest,
                completed_at=datetime.now(timezone.utc),
            )
        self.assertEqual(missing.exception.code, "steam_link_missing")


if __name__ == "__main__":
    unittest.main()
