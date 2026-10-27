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
            self.pool, "trucky", "job-991234560", user, "100", "km",
            provider_account_id="991234560",
        ))
        self.assertFalse(await profiles.record_normalized_provider_job(
            self.pool, "trucky", "job-991234560", user, "100", "km",
            provider_account_id="991234560",
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
                self.pool, "trucky", "job-991234560", user, "101", "km",
                provider_account_id="991234560",
            )
        with self.assertRaisesRegex(ValueError, "stable provider account ID"):
            await profiles.record_normalized_provider_job(
                self.pool, "trucky", "job-without-account", user, "100", "km"
            )

    async def test_trucksbook_identity_backfill_preserves_legacy_mileage_and_jobs(self):
        """V2 backfill is additive; legacy history remains the source of truth."""
        user = 991234560111
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS driver_links (
                    discord_user_id BIGINT PRIMARY KEY,
                    trucksbook_user_id BIGINT,
                    trucksbook_name TEXT NOT NULL,
                    linked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS driver_progress (
                    discord_user_id BIGINT PRIMARY KEY,
                    trucksbook_name TEXT NOT NULL UNIQUE,
                    real_miles BIGINT NOT NULL DEFAULT 0,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_jobs (
                    job_id TEXT PRIMARY KEY,
                    discord_user_id BIGINT NOT NULL,
                    trucksbook_name TEXT NOT NULL,
                    accepted_distance INTEGER NOT NULL,
                    statistics TEXT NOT NULL,
                    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            await conn.execute("""
                INSERT INTO driver_links(discord_user_id,trucksbook_user_id,trucksbook_name)
                VALUES($1,991234560112,'verified-candidate') ON CONFLICT DO NOTHING
            """, user)
            await conn.execute("""
                INSERT INTO driver_progress(discord_user_id,trucksbook_name,real_miles)
                VALUES($1,'verified-candidate',12345) ON CONFLICT DO NOTHING
            """, user)
            await conn.execute("""
                INSERT INTO processed_jobs(job_id,discord_user_id,trucksbook_name,
                    accepted_distance,statistics)
                VALUES('preserve-job-991234560113',$1,'verified-candidate',176,'Real')
                ON CONFLICT DO NOTHING
            """, user)

        await profiles.backfill_trucksbook_profiles(self.pool)
        await profiles.backfill_trucksbook_profiles(self.pool)
        import unified_staging_shadow_receipts_v2 as shadow
        from unittest.mock import patch
        with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "YES"}):
            await shadow.install_schema(self.pool)
            await shadow.install_schema(self.pool)
        async with self.pool.acquire() as conn:
            totals = await conn.fetchrow("""
                SELECT (SELECT real_miles FROM driver_progress WHERE discord_user_id=$1)
                           AS historical_miles,
                       (SELECT COUNT(*) FROM processed_jobs
                         WHERE job_id='preserve-job-991234560113') AS preserved_jobs,
                       (SELECT COUNT(*) FROM unified_provider_links
                         WHERE provider='trucksbook' AND provider_account_id='991234560112'
                           AND discord_user_id=$1) AS linked_once
            """, user)
        self.assertEqual(totals["historical_miles"], 12345)
        self.assertEqual(totals["preserved_jobs"], 1)
        self.assertEqual(totals["linked_once"], 1)

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
        async with self.pool.acquire() as conn:
            job_count_before = await conn.fetchval(
                "SELECT COUNT(*) FROM unified_provider_job_events"
            )
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
        self.assertEqual(job_count, job_count_before)

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
        self.assertTrue(await profiles.record_normalized_provider_job(
            self.pool, "trucky", "time-991234590", user, "100", "km",
            provider_account_id="991234590", delivered_at=delivered))
        self.assertFalse(await profiles.record_normalized_provider_job(
            self.pool, "trucky", "time-991234590", user, "100", "km",
            provider_account_id="991234590", delivered_at=delivered))
        with self.assertRaises(ValueError):
            await profiles.record_normalized_provider_job(
                self.pool, "trucky", "time-991234590", user, "100", "km",
                provider_account_id="991234590",
                delivered_at=delivered + timedelta(minutes=10))

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
            self.pool, "trucksbook", "ledger-991234580", user, "100", "km",
            provider_account_id="991234580")
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
            "100", "km", provider_account_id="991234570",
            delivered_at=now, job_metadata=meta)
        await profiles.record_normalized_provider_job(
            self.pool, "trucksbook", "match-trucksbook-991234571", user,
            "62.14", "mi", provider_account_id="991234571",
            delivered_at=now + timedelta(minutes=10),
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
        async with self.pool.acquire() as conn:
            evidence = await conn.fetchrow("""
                SELECT ownership_evidence_kind,ownership_evidence_sha256
                FROM unified_provider_claims
                WHERE discord_user_id=$1 AND provider='trucky'
            """, user)
            receipt = await conn.fetchval("""
                SELECT review_status FROM unified_trucky_webhook_receipts
                WHERE payload_sha256=$1
            """, digest)
        self.assertEqual(evidence["ownership_evidence_kind"], "trucky_signed_job_steam_match")
        self.assertEqual(evidence["ownership_evidence_sha256"], digest)
        self.assertEqual(receipt, "verified")

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

    async def test_trucksbook_pending_flow_requires_steam_and_never_links_from_id_alone(self):
        user = 991234567890123570
        steam = "76561198000000070"
        await profiles.begin_registration(self.pool, user, 991234567890123571)
        with self.assertRaisesRegex(ValueError, "Verify Steam"):
            await profiles.submit_trucksbook_pending_claim(self.pool, user, "68097217")
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO unified_steam_links(discord_user_id,steam_id64)
                VALUES($1,$2)
            """, user, steam)
        result = await profiles.submit_trucksbook_pending_claim(
            self.pool, user, "68097217"
        )
        self.assertEqual(result["status"], "pending_verification")
        async with self.pool.acquire() as conn:
            claim = await conn.fetchrow("""
                SELECT status,membership_confirmed,ownership_confirmed,
                       ownership_evidence_kind,ownership_evidence_sha256
                FROM unified_provider_claims
                WHERE discord_user_id=$1 AND provider='trucksbook'
            """, user)
            linked = await conn.fetchval("""
                SELECT EXISTS(SELECT 1 FROM unified_provider_links
                  WHERE provider='trucksbook' AND discord_user_id=$1)
            """, user)
            profile_status = await conn.fetchval(
                "SELECT status FROM unified_driver_profiles WHERE discord_user_id=$1", user
            )
        self.assertEqual(claim["status"], "pending")
        self.assertFalse(claim["membership_confirmed"])
        self.assertFalse(claim["ownership_confirmed"])
        self.assertIsNone(claim["ownership_evidence_kind"])
        self.assertIsNone(claim["ownership_evidence_sha256"])
        self.assertFalse(linked)
        self.assertEqual(profile_status, "pending")

    async def test_retired_trucksbook_steam_review_never_approves_claim(self):
        user = 991234567890123572
        reviewer = 991234567890123573
        steam = "76561198000000072"
        digest = hashlib.sha256(b"unsupported witnessed review").hexdigest()
        await profiles.begin_registration(self.pool, user, 991234567890123574)
        async with self.pool.acquire() as conn:
            await conn.execute("INSERT INTO unified_steam_links(discord_user_id,steam_id64) VALUES($1,$2)", user, steam)
        await profiles.submit_trucksbook_pending_claim(self.pool, user, "68097272")
        with self.assertRaisesRegex(ValueError, "pending provider-supported evidence"):
            await profiles.approve_trucksbook_claim_after_witness(
                self.pool, user, reviewer, steam, "68097272", digest,
                membership_confirmed=True,
            )
        async with self.pool.acquire() as conn:
            claim = await conn.fetchrow("SELECT status,ownership_confirmed FROM unified_provider_claims WHERE discord_user_id=$1 AND provider='trucksbook'", user)
            self.assertEqual(claim["status"], "pending")
            self.assertFalse(claim["ownership_confirmed"])
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM unified_provider_links WHERE provider='trucksbook' AND discord_user_id=$1", user), 0)

    async def test_boolean_only_trucksbook_approval_is_rejected(self):
        user = 991234567890123575
        with self.assertRaisesRegex(ValueError, "pending provider-supported verification"):
            await profiles.approve_provider_claim(
                self.pool, user, "trucksbook", user + 1,
                membership_confirmed=True, ownership_confirmed=True,
            )

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

    async def test_real_paris_lille_pair_stays_pending_without_authoritative_job_records(self):
        import unified_manual_job_reconciliation_v2 as review
        from unittest.mock import patch
        user = 991234567890123499
        with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "YES"}):
            case = await review.open_potential_duplicate(
                self.pool, "27848310", "68093174", user
            )
            with self.assertRaisesRegex(ValueError, "Both job owners must be verified"):
                await review.resolve_review_case(
                    self.pool, "27848310", "68093174", "confirmed_duplicate",
                    user, "independent_telemetry", hashlib.sha256(b"review evidence").hexdigest(),
                )
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT status,discord_user_id FROM unified_job_reconciliation_cases
                WHERE trucky_job_id='27848310' AND trucksbook_job_id='68093174'
            """)
            audit_count = await conn.fetchval("""
                SELECT COUNT(*) FROM unified_job_reconciliation_audit WHERE case_id=$1
            """, case["case_id"])
            event_count = await conn.fetchval("""
                SELECT COUNT(*) FROM unified_provider_job_events
                WHERE (provider='trucky' AND provider_job_id='27848310')
                   OR (provider='trucksbook' AND provider_job_id='68093174')
            """)
        self.assertEqual(row["status"], "potential_duplicate")
        self.assertIsNone(row["discord_user_id"])
        self.assertEqual(audit_count, 1)
        self.assertEqual(event_count, 0)

    async def test_manual_review_requires_verified_same_driver_jobs_and_only_audits(self):
        import unified_manual_job_reconciliation_v2 as review
        from unittest.mock import patch
        from decimal import Decimal
        user = 991234567890123498
        accounts = {"trucky": "887766551", "trucksbook": "887766552"}
        async with self.pool.acquire() as conn:
            await conn.execute("INSERT INTO unified_driver_profiles(discord_user_id) VALUES($1)", user)
            await conn.execute("""
                INSERT INTO unified_steam_links(discord_user_id,steam_id64,verified_at)
                VALUES($1,'76561198012345679',NOW())
            """, user)
            for provider, account in accounts.items():
                await conn.execute("""
                    INSERT INTO unified_provider_links
                        (provider,provider_account_id,discord_user_id,verified_at)
                    VALUES($1,$2,$3,NOW())
                """, provider, account, user)
                await conn.execute("""
                    INSERT INTO unified_provider_claims
                        (discord_user_id,provider,provider_account_id,
                         membership_confirmed,ownership_confirmed,status,reviewed_at)
                    VALUES($1,$2,$3,TRUE,TRUE,'approved',NOW())
                """, user, provider, account)
        event_time = datetime(2026, 10, 18, 20, 29, tzinfo=timezone.utc)
        delivery_metadata = {
            "game": "ETS2", "origin": "Paris", "destination": "Lille",
            "cargo": "Pet Food",
        }
        await profiles.record_normalized_provider_job(
            self.pool, "trucky", "8877665531", user, "284", "km",
            provider_account_id=accounts["trucky"], delivered_at=event_time,
            job_metadata=delivery_metadata,
        )
        await profiles.record_normalized_provider_job(
            self.pool, "trucksbook", "8877665532", user, "176", "mi",
            provider_account_id=accounts["trucksbook"], delivered_at=event_time,
            job_metadata=delivery_metadata,
        )
        digest = hashlib.sha256(b"independently reviewed staging evidence").hexdigest()
        with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "YES"}):
            case = await review.open_potential_duplicate(
                self.pool, "8877665531", "8877665532", user
            )
            with self.assertRaisesRegex(ValueError, "Both job owners must be verified"):
                await review.resolve_review_case(
                    self.pool, "8877665531", "8877665532",
                    "confirmed_duplicate", user, "independent_telemetry", digest,
                )
        async with self.pool.acquire() as conn:
            status = await conn.fetchval("""
                SELECT status FROM unified_job_reconciliation_cases WHERE case_id=$1
            """, case["case_id"])
            audit = await conn.fetchrow("""
                SELECT decision,evidence_sha256 FROM unified_job_reconciliation_audit
                WHERE case_id=$1 ORDER BY audit_id DESC LIMIT 1
            """, case["case_id"])
            miles = await conn.fetchrow("""
                SELECT SUM(real_miles) AS real,SUM(race_miles) AS race
                FROM unified_provider_job_events WHERE provider_job_id IN
                    ('8877665531','8877665532')
            """)
        self.assertEqual(status, "potential_duplicate")
        self.assertEqual(audit["decision"], "potential_duplicate")
        self.assertIsNone(audit["evidence_sha256"])
        # Review leaves both immutable source observations in the ledger and does not credit either.
        self.assertEqual(miles["real"], Decimal("352.47"))

    async def test_manual_review_rejects_provider_account_mismatch_and_nonstaging(self):
        import unified_manual_job_reconciliation_v2 as review
        from unittest.mock import patch
        user = 991234567890123497
        async with self.pool.acquire() as conn:
            await conn.execute("INSERT INTO unified_driver_profiles(discord_user_id) VALUES($1)", user)
            await conn.execute("""
                INSERT INTO unified_provider_links
                    (provider,provider_account_id,discord_user_id,verified_at)
                VALUES('trucky','887766553',$1,NOW())
            """, user)
        with self.assertRaisesRegex(RuntimeError, "staging-only"):
            with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "NO"}):
                await review.open_potential_duplicate(self.pool, "1", "2", user)
        with self.assertRaisesRegex(ValueError, "Provider account not verified"):
            await profiles.record_normalized_provider_job(
                self.pool, "trucky", "wrong-link-job", user, "10", "mi",
                provider_account_id="887766554",
            )


    async def test_trucksbook_shadow_receipt_is_redacted_and_idempotent(self):
        import unified_staging_shadow_receipts_v2 as shadow
        from unittest.mock import patch
        with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "YES"}):
            await shadow.install_schema(self.pool)
        payload = [{"title": "Job delivery #68093174",
                    "description": "private display content never persisted"}]
        parsed = {"job_id": "68093174", "statistics": "Real, WoTr",
                  "accepted_distance": 176}
        with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "YES"}):
            first = await shadow.record_trucksbook_shadow_receipt(
                self.pool, message_id=991234567001, webhook_id=456789,
                configured_webhook_id="456789", payload=payload, parsed_job=parsed,
            )
            replay = await shadow.record_trucksbook_shadow_receipt(
                self.pool, message_id=991234567001, webhook_id=456789,
                configured_webhook_id="456789", payload=payload, parsed_job=parsed,
            )
            concurrent_replays = await asyncio.gather(*(
                shadow.record_trucksbook_shadow_receipt(
                    self.pool, message_id=991234567001, webhook_id=456789,
                    configured_webhook_id="456789", payload=payload, parsed_job=parsed,
                ) for _ in range(5)
            ))
        self.assertEqual(first, {"status": "quarantined", "duplicate": False,
                                 "delivery_count": 1})
        self.assertEqual(replay, {"status": "quarantined", "duplicate": True,
                                  "delivery_count": 2})
        self.assertTrue(all(item["duplicate"] for item in concurrent_replays))
        self.assertEqual(sorted(item["delivery_count"] for item in concurrent_replays),
                         [3, 4, 5, 6, 7])
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT source_payload_sha256, provider_job_id, mileage_classification,
                       candidate_distance_miles, delivery_count, ownership_status,
                       mileage_awarded, registration_changed, roles_changed
                FROM staging_shadow_receipts WHERE source_delivery_id=$1
            """, "991234567001")
            columns = await conn.fetchval("""
                SELECT COUNT(*) FROM information_schema.columns
                WHERE table_schema='public' AND table_name='staging_shadow_receipts'
                  AND column_name IN ('raw_payload','display_name','discord_user_id')
            """)
        self.assertEqual(row["provider_job_id"], "68093174")
        self.assertEqual(row["mileage_classification"], "real")
        self.assertEqual(row["candidate_distance_miles"], 176)
        self.assertEqual(row["delivery_count"], 7)
        self.assertEqual(row["ownership_status"], "unverified")
        self.assertFalse(row["mileage_awarded"])
        self.assertFalse(row["registration_changed"])
        self.assertFalse(row["roles_changed"])
        self.assertEqual(columns, 0)
        with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "YES"}):
            # A provider resend may arrive as a new Discord message ID, in
            # addition to a gateway replay of the original message ID.
            await shadow.record_trucksbook_shadow_receipt(
                self.pool, message_id=991234567004, webhook_id=456789,
                configured_webhook_id="456789", payload=payload, parsed_job=parsed,
            )
            report = await shadow.audit_receipts(self.pool, "68093174")
        self.assertEqual(report["receipt_count"], 2)
        self.assertEqual(report["unique_jobs"], 1)
        self.assertEqual(report["replay_count"], 7)
        self.assertEqual(report["source_verified"], 2)
        self.assertEqual(report["safety_intact"], 2)
        self.assertEqual(report["safety_violations"], 0)
        self.assertEqual(report["job"]["provider_job_id"], "68093174")
        self.assertEqual(report["job"]["receipt_count"], 2)
        self.assertEqual(report["job"]["replay_count"], 7)

    async def test_shadow_audit_is_staging_only_and_rejects_invalid_job_lookup(self):
        import unified_staging_shadow_receipts_v2 as shadow
        from unittest.mock import patch
        with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "NO"}):
            with self.assertRaisesRegex(RuntimeError, "isolated staging"):
                await shadow.audit_receipts(self.pool)
        with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "YES"}):
            with self.assertRaisesRegex(ValueError, "Invalid TrucksBook job ID"):
                await shadow.audit_receipts(self.pool, "../raw")

    async def test_trucksbook_shadow_receipt_rejects_bad_source_and_nonstaging(self):
        import unified_staging_shadow_receipts_v2 as shadow
        from unittest.mock import patch
        with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "YES"}):
            await shadow.install_schema(self.pool)
        with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "NO"}):
            with self.assertRaisesRegex(RuntimeError, "isolated staging"):
                await shadow.record_trucksbook_shadow_receipt(
                    self.pool, message_id=991234567002, webhook_id=456789,
                    configured_webhook_id="456789", payload=[], parsed_job=None,
                )
        with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "YES"}):
            with self.assertRaisesRegex(ValueError, "not configured"):
                await shadow.record_trucksbook_shadow_receipt(
                    self.pool, message_id=991234567003, webhook_id=456789,
                    configured_webhook_id="999999", payload=[], parsed_job=None,
                )


    async def test_concurrent_staging_duplicate_review_opens_are_idempotent(self):
        import unified_manual_job_reconciliation_v2 as review
        from unittest.mock import patch
        trucky_id, trucksbook_id = "198765432101", "198765432102"
        reviewer_id = 991234567111
        with patch.dict(os.environ, {"AT_V2_ISOLATED_STAGING": "YES"}):
            opened = await asyncio.gather(*(
                review.open_potential_duplicate(
                    self.pool, trucky_id, trucksbook_id, reviewer_id
                ) for _ in range(8)
            ))
        self.assertEqual(sum(bool(case["created"]) for case in opened), 1)
        self.assertEqual(len({case["case_id"] for case in opened}), 1)
        async with self.pool.acquire() as conn:
            rows = await conn.fetchrow("""
                SELECT
                    (SELECT COUNT(*) FROM unified_job_reconciliation_cases
                     WHERE trucky_job_id=$1 AND trucksbook_job_id=$2) AS cases,
                    (SELECT COUNT(*) FROM unified_job_reconciliation_audit a
                     JOIN unified_job_reconciliation_cases c USING(case_id)
                     WHERE c.trucky_job_id=$1 AND c.trucksbook_job_id=$2) AS audits
            """, trucky_id, trucksbook_id)
        self.assertEqual(rows["cases"], 1)
        self.assertEqual(rows["audits"], 1)

    async def test_authenticated_trucksbook_staff_review_preserves_identity_and_rejects_mismatch(self):
        from unittest.mock import patch
        user, reviewer, account = 991234567890125001, 991234567890125002, '991234567890125003'
        steam = '76561198099995001'
        async with self.pool.acquire() as conn:
            await conn.execute("INSERT INTO unified_driver_profiles(discord_user_id) VALUES($1) ON CONFLICT DO NOTHING",user)
            await conn.execute("INSERT INTO unified_steam_links(discord_user_id,steam_id64) VALUES($1,$2) ON CONFLICT DO NOTHING",user,steam)
            await conn.execute("INSERT INTO unified_provider_claims(discord_user_id,provider,provider_account_id) VALUES($1,'trucksbook',$2) ON CONFLICT DO NOTHING",user,account)
            before = await conn.fetchrow("SELECT * FROM unified_driver_profiles WHERE discord_user_id=$1",user)
        with patch.dict(os.environ, {'AT_V2_ISOLATED_STAGING':'YES'}):
            with self.assertRaisesRegex(ValueError,'does not match verified'):
                await profiles.review_trucksbook_authenticated_profile(self.pool,user,reviewer,'76561198099995002',account,'a'*64,witnessed_profile_mapping=True)
            report = await profiles.audit_trucksbook_observed_identity(self.pool,user,account,hashlib.sha256(steam.encode()).hexdigest())
            self.assertTrue(report['observed_steam_match'])
            self.assertEqual(report['claim_status'],'pending')
            result = await profiles.review_trucksbook_authenticated_profile(self.pool,user,reviewer,steam,account,'a'*64,witnessed_profile_mapping=True)
            self.assertFalse(result['mileage_eligible'])
            # Retry reattests the same link; uniqueness prevents extra accounts.
            await profiles.review_trucksbook_authenticated_profile(self.pool,user,reviewer,steam,account,'a'*64,witnessed_profile_mapping=True)
        async with self.pool.acquire() as conn:
            after = await conn.fetchrow("SELECT * FROM unified_driver_profiles WHERE discord_user_id=$1",user)
            self.assertEqual(dict(before),dict(after))
            self.assertEqual(await conn.fetchval("SELECT COUNT(*) FROM unified_provider_links WHERE provider='trucksbook' AND discord_user_id=$1",user),1)
            claim = await conn.fetchrow("SELECT status,ownership_evidence_kind,reviewed_by,ownership_review_history FROM unified_provider_claims WHERE discord_user_id=$1 AND provider='trucksbook'",user)
            self.assertEqual(claim['status'],'approved')
            self.assertEqual(claim['reviewed_by'],reviewer)
            self.assertEqual(len(json.loads(claim['ownership_review_history'])),1)
            self.assertEqual(claim['ownership_evidence_kind'],'trucksbook_staff_reviewed_profile_steam_mapping')
            self.assertEqual(await conn.fetchval("SELECT steam_id64 FROM unified_steam_links WHERE discord_user_id=$1",user),steam)


    async def test_full_ordinary_registration_and_steam_embed_callback_without_staff_review(self):
        from unittest.mock import AsyncMock, patch
        from urllib.parse import urlsplit, parse_qs
        from aiohttp.test_utils import TestClient, TestServer
        from trucky_staging_receiver_v2 import create_integrations_app
        import unified_registration_workflow_v2 as registration
        import steam_openid_v2
        user=991234567890126001
        env={'AT_V2_ISOLATED_STAGING':'YES', 'AT_V2_STAGING_REGISTRATION_ENABLED':'YES',
             'RAILWAY_ENVIRONMENT_ID':registration.STAGING_ENVIRONMENT,
             'AT_STEAM_CALLBACK_URL':'https://example.test/auth/steam/callback'}
        await profiles.begin_registration(self.pool,user,991234567890126002)
        initial=await registration.state(self.pool,user)
        self.assertEqual(initial['steam_status'],'Not Linked')
        self.assertFalse(initial['ready'])
        callback_url=await profiles.create_steam_auth_state(self.pool,user,env['AT_STEAM_CALLBACK_URL'])
        token=parse_qs(urlsplit(callback_url).query)['state'][0]
        notify=AsyncMock()
        client=TestClient(TestServer(create_integrations_app(self.pool,on_steam_verified=notify)))
        await client.start_server()
        query={'state':token,'openid.return_to':callback_url,'openid.response_nonce':'registration-flow-unique-nonce'}
        try:
            with patch.dict(os.environ,env),patch.object(steam_openid_v2,'verify_steam_callback',AsyncMock(return_value='76561198099996001')):
                response=await client.get('/auth/steam/callback',params=query)
                self.assertEqual(response.status,200,await response.text())
                notify.assert_awaited_once_with(user)
                steam_only=await registration.state(self.pool,user)
                self.assertEqual(steam_only['steam_status'],'Verified')
                self.assertFalse(steam_only['ready'])
                # Provider lookups are stubbed canonical names, NOT live ownership proof.
                await registration.link_supported_account(self.pool,user,'trucksbook','991234567890126003','Test Driver')
                tb=await registration.state(self.pool,user)
                self.assertEqual(tb['accounts']['trucksbook']['status'],'Linked')
                self.assertTrue(tb['ready'])
                self.assertEqual(tb['session']['status'],'approved')
                await registration.link_supported_account(self.pool,user,'trucky','991234567890126004','Test Driver')
                await registration.link_supported_account(self.pool,user,'trucky','991234567890126004','Test Driver')
                final=await registration.state(self.pool,user)
                self.assertEqual([a['status'] for a in final['accounts'].values()],['Linked','Linked'])
                self.assertTrue(final['ready'])
                with self.assertRaisesRegex(ValueError,'existing account'):
                    await registration.link_supported_account(self.pool,user,'trucky','991234567890126099','Different Account')
                replay=await client.get('/auth/steam/callback',params=query)
                self.assertEqual(replay.status,400)
                notify.assert_awaited_once_with(user)
                # Reopening /register updates the channel without erasing links/status.
                await profiles.begin_registration(self.pool,user,991234567890126005)
                self.assertTrue((await registration.state(self.pool,user))['ready'])
            async with self.pool.acquire() as conn:
                rows=await conn.fetch('SELECT status,ownership_confirmed,reviewed_by FROM unified_provider_claims WHERE discord_user_id=$1',user)
                self.assertEqual(len(rows),2)
                self.assertTrue(all(r['status']=='pending' and not r['ownership_confirmed'] and r['reviewed_by'] is None for r in rows))
                self.assertEqual(await conn.fetchval('SELECT COUNT(*) FROM unified_provider_links WHERE discord_user_id=$1 AND verified_at IS NOT NULL',user),0)
                self.assertEqual(await conn.fetchval('SELECT COUNT(*) FROM unified_provider_job_events WHERE discord_user_id=$1',user),0)
        finally:
            await client.close()



    async def test_admin_overview_pagination_preserves_registration_records(self):
        from unittest.mock import patch
        import unified_registration_workflow_v2 as registration
        from tests.test_registration_workflow_v2 import ENV
        user = 991234567890127010
        await profiles.begin_registration(self.pool,user,991234567890127011)
        async with self.pool.acquire() as conn:
            await conn.execute("INSERT INTO unified_steam_links(discord_user_id,steam_id64) VALUES($1,'76561198099997010') ON CONFLICT DO NOTHING", user)
            await conn.execute("INSERT INTO unified_provider_links(provider,provider_account_id,discord_user_id) VALUES('trucksbook','991234567890127012',$1) ON CONFLICT DO NOTHING", user)
            before = dict(await conn.fetchrow("SELECT * FROM unified_driver_profiles WHERE discord_user_id=$1", user))
        with patch.dict(os.environ, ENV):
            first = await registration.list_registration_statuses(self.pool, 1, 2)
            all_rows = []
            for page in range(1, first["pages"] + 1):
                result = await registration.list_registration_statuses(self.pool, page, 2)
                self.assertLessEqual(len(result["members"]),2)
                all_rows += result["members"]
            target = next(row for row in all_rows if row["discord_user_id"] == user)
            self.assertEqual(target["steam"], "Verified")
            self.assertEqual(target["trucksbook"], "Linked")
            self.assertEqual(target["trucky"], "Not Linked")
            self.assertEqual(target["registration"], "Incomplete")
            self.assertEqual(len({r["discord_user_id"] for r in all_rows}),first["total"])
            with self.assertRaisesRegex(ValueError, "does not exist"):
                await registration.list_registration_statuses(self.pool, first["pages"] + 1, 2)
        async with self.pool.acquire() as conn:
            self.assertEqual(before,dict(await conn.fetchrow("SELECT * FROM unified_driver_profiles WHERE discord_user_id=$1", user)))
            self.assertEqual(await conn.fetchval("SELECT status FROM unified_registration_sessions WHERE discord_user_id=$1",user),"pending")
            self.assertIsNone(await conn.fetchval("SELECT verified_at FROM unified_provider_links WHERE discord_user_id=$1 AND provider='trucksbook'", user))



    async def _legacy_cancel_fixture(self, user, channel):
        await self.conn.execute('''CREATE TABLE IF NOT EXISTS driver_verifications (
            discord_user_id BIGINT PRIMARY KEY,verification_channel_id BIGINT,
            status TEXT,completed_at TIMESTAMPTZ)''')
        await self.conn.execute('''INSERT INTO driver_verifications
            (discord_user_id,verification_channel_id,status) VALUES($1,$2,'pending')
            ON CONFLICT(discord_user_id) DO UPDATE SET
            verification_channel_id=EXCLUDED.verification_channel_id,status='pending' ''', user, channel)

    async def test_cancel_invalidates_steam_links_without_removing_accounts(self):
        from urllib.parse import urlsplit, parse_qs
        from unittest.mock import patch
        import unified_registration_workflow_v2 as workflow
        from tests.test_registration_workflow_v2 import ENV
        user, channel = 991234568881001, 991234568881002
        steam = '76561198888888001'
        await profiles.begin_registration(self.pool, user, channel)
        await self._legacy_cancel_fixture(user, channel)
        url = await profiles.create_steam_auth_state(self.pool, user, 'https://example.test/auth/steam/callback')
        token = parse_qs(urlsplit(url).query)['state'][0]
        digest = hashlib.sha256(token.encode()).hexdigest()
        with patch.dict(os.environ, ENV, clear=True):
            await workflow.cancel_registration(self.pool, user, channel)
            await workflow.cancel_registration(self.pool, user, channel)
        with self.assertRaises(ValueError):
            await profiles.bind_verified_steam(self.pool, user, steam, digest, 'cancelled-nonce-881001')
        with self.assertRaises(ValueError):
            await profiles.create_steam_auth_state(self.pool, user, 'https://example.test/auth/steam/callback')
        self.assertIsNone(await self.conn.fetchval('SELECT steam_id64 FROM unified_steam_links WHERE discord_user_id=$1', user))
        self.assertIsNotNone(await self.conn.fetchval('SELECT consumed_at FROM unified_steam_auth_states WHERE state_hash=$1', digest))
        await profiles.begin_registration(self.pool, user, channel + 1)
        new = await profiles.create_steam_auth_state(self.pool, user, 'https://example.test/auth/steam/callback')
        new_digest = hashlib.sha256(parse_qs(urlsplit(new).query)['state'][0].encode()).hexdigest()
        await profiles.bind_verified_steam(self.pool, user, steam, new_digest, 'new-nonce-881001')
        with patch.dict(os.environ, ENV, clear=True):
            await workflow.link_supported_account(self.pool, user, 'trucksbook', '998881001', 'Test Driver')
            await workflow.cancel_registration(self.pool, user, channel + 1)
            self.assertFalse((await workflow.state(self.pool, user))['ready'])
        self.assertEqual(await self.conn.fetchval('SELECT steam_id64 FROM unified_steam_links WHERE discord_user_id=$1', user), steam)
        self.assertEqual(await self.conn.fetchval('SELECT COUNT(*) FROM unified_provider_links WHERE discord_user_id=$1', user), 1)
        self.assertEqual(await self.conn.fetchval('SELECT status FROM unified_driver_profiles WHERE discord_user_id=$1', user), 'active')

    async def test_linked_trucky_account_upgrades_with_signed_evidence_idempotently(self):
        from unittest.mock import patch
        import unified_registration_workflow_v2 as workflow
        from tests.test_registration_workflow_v2 import ENV
        user, channel = 991234568881010, 991234568881011
        steam, account, job = '76561198888888010', '998881010', '998881011'
        await profiles.begin_registration(self.pool, user, channel)
        await self.conn.execute('INSERT INTO unified_steam_links(discord_user_id,steam_id64) VALUES($1,$2)', user, steam)
        with patch.dict(os.environ, ENV, clear=True):
            await workflow.link_supported_account(self.pool, user, 'trucky', account, 'Test Driver')
            self.assertEqual((await workflow.state(self.pool, user))['accounts']['trucky']['status'], 'Linked')
        count = await self.conn.fetchval('SELECT COUNT(*) FROM unified_provider_job_events')
        digest = hashlib.sha256(b'synthetic-signed-upgrade-fixture').hexdigest()
        await profiles.record_trucky_webhook_receipt(self.pool, digest, 'job_completed', job)
        for _ in range(2):
            self.assertEqual(await profiles.auto_approve_trucky_if_steam_matches(
                self.pool, None, account, steam, trusted_provider_source=True,
                membership_confirmed=True, provider_job_id=job, payload_sha256=digest,
                completed_at=datetime.now(timezone.utc)), user)
        with patch.dict(os.environ, ENV, clear=True):
            state = await workflow.state(self.pool, user)
        self.assertEqual(state['accounts']['trucky']['status'], 'Verified')
        self.assertTrue(state['ready'])
        self.assertEqual(await self.conn.fetchval('SELECT COUNT(*) FROM unified_provider_links WHERE discord_user_id=$1', user), 1)
        self.assertEqual(await self.conn.fetchval('SELECT COUNT(*) FROM unified_provider_job_events'), count)
        await self.conn.execute("UPDATE unified_driver_profiles SET status='suspended' WHERE discord_user_id=$1", user)
        with self.assertRaises(ValueError):
            await profiles.auto_approve_trucky_if_steam_matches(self.pool, None, account, steam,
                trusted_provider_source=True, membership_confirmed=True,
                provider_job_id=job, payload_sha256=digest, completed_at=datetime.now(timezone.utc))
        self.assertEqual(await self.conn.fetchval('SELECT status FROM unified_driver_profiles WHERE discord_user_id=$1', user), 'suspended')

    async def test_cancellation_and_steam_callback_serialize_without_deadlock(self):
        from urllib.parse import urlsplit, parse_qs
        from unittest.mock import patch
        import unified_registration_workflow_v2 as workflow
        from tests.test_registration_workflow_v2 import ENV
        user, channel = 991234568881020, 991234568881021
        await profiles.begin_registration(self.pool, user, channel)
        await self._legacy_cancel_fixture(user, channel)
        url = await profiles.create_steam_auth_state(self.pool, user, 'https://example.test/auth/steam/callback')
        digest = hashlib.sha256(parse_qs(urlsplit(url).query)['state'][0].encode()).hexdigest()
        with patch.dict(os.environ, ENV, clear=True):
            results = await asyncio.wait_for(asyncio.gather(
                workflow.cancel_registration(self.pool, user, channel),
                profiles.bind_verified_steam(self.pool, user, '76561198888888020', digest, 'race-nonce-881020'),
                return_exceptions=True), timeout=10)
        self.assertIs(results[0], True)
        self.assertTrue(results[1] is None or isinstance(results[1], ValueError), results)
        self.assertEqual(await self.conn.fetchval('SELECT status FROM unified_registration_sessions WHERE discord_user_id=$1', user), 'cancelled')
        self.assertIsNotNone(await self.conn.fetchval('SELECT consumed_at FROM unified_steam_auth_states WHERE state_hash=$1', digest))
        with self.assertRaises(ValueError):
            await profiles.bind_verified_steam(self.pool, user, '76561198888888020', digest, 'race-nonce-retry-881020')



    async def test_confirm_completes_only_matching_onboarding_and_preserves_links(self):
        from unittest.mock import patch
        import unified_registration_workflow_v2 as workflow
        from tests.test_registration_workflow_v2 import ENV
        user, channel = 991234568882001, 991234568882002
        await profiles.begin_registration(self.pool, user, channel)
        await self._legacy_cancel_fixture(user, channel)
        await self.conn.execute('INSERT INTO unified_steam_links(discord_user_id,steam_id64) VALUES($1,$2)', user, '76561198888888201')
        with patch.dict(os.environ, ENV, clear=True):
            await workflow.link_supported_account(self.pool, user, 'trucksbook', '998882001', 'Retained Driver')
            before = dict(await self.conn.fetchrow("SELECT * FROM unified_provider_links WHERE discord_user_id=$1", user))
            self.assertFalse(await workflow.complete_if_ready(self.pool, user, channel_id=channel+1))
            self.assertEqual(await self.conn.fetchval('SELECT status FROM driver_verifications WHERE discord_user_id=$1',user),'pending')
            self.assertTrue(await workflow.complete_if_ready(self.pool, user, channel_id=channel))
            completed_at = await self.conn.fetchval('SELECT completed_at FROM driver_verifications WHERE discord_user_id=$1',user)
            self.assertTrue(await workflow.complete_if_ready(self.pool, user, channel_id=channel))
        self.assertEqual(await self.conn.fetchval('SELECT status FROM driver_verifications WHERE discord_user_id=$1',user),'completed')
        self.assertEqual(await self.conn.fetchval('SELECT completed_at FROM driver_verifications WHERE discord_user_id=$1',user),completed_at)
        self.assertEqual(dict(await self.conn.fetchrow('SELECT * FROM unified_provider_links WHERE discord_user_id=$1',user)),before)

    async def test_either_provider_completes_without_changing_historical_mileage(self):
        from unittest.mock import patch
        import unified_registration_workflow_v2 as workflow
        from tests.test_registration_workflow_v2 import ENV
        await self.conn.execute("""CREATE TABLE IF NOT EXISTS driver_progress (
            discord_user_id BIGINT PRIMARY KEY,trucksbook_name TEXT NOT NULL UNIQUE,
            real_miles BIGINT NOT NULL DEFAULT 0,updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        await self.conn.execute("""CREATE TABLE IF NOT EXISTS processed_jobs (
            job_id TEXT PRIMARY KEY,discord_user_id BIGINT NOT NULL,trucksbook_name TEXT NOT NULL,
            accepted_distance INTEGER NOT NULL,statistics TEXT NOT NULL,
            processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        for index, provider in enumerate(('trucksbook','trucky')):
            with self.subTest(provider=provider):
                user=991234568883001+index
                channel=991234568883011+index
                name=f'single-provider-preserve-{index}'
                job=f'single-provider-history-{index}'
                await self.conn.execute('INSERT INTO driver_progress(discord_user_id,trucksbook_name,real_miles) VALUES($1,$2,12345)',user,name)
                await self.conn.execute("""INSERT INTO processed_jobs(job_id,discord_user_id,trucksbook_name,accepted_distance,statistics)
                    VALUES($1,$2,$3,176,'Real')""",job,user,name)
                before_progress=dict(await self.conn.fetchrow('SELECT * FROM driver_progress WHERE discord_user_id=$1',user))
                before_job=dict(await self.conn.fetchrow('SELECT * FROM processed_jobs WHERE job_id=$1',job))
                await profiles.begin_registration(self.pool,user,channel)
                await self._legacy_cancel_fixture(user,channel)
                await self.conn.execute('INSERT INTO unified_steam_links(discord_user_id,steam_id64) VALUES($1,$2)',user,str(76561198888888301+index))
                with patch.dict(os.environ,ENV,clear=True):
                    self.assertTrue(await workflow.link_supported_account(self.pool,user,provider,str(998883001+index),name))
                    self.assertTrue(await workflow.complete_if_ready(self.pool,user,channel_id=channel))
                    state=await workflow.state(self.pool,user)
                self.assertTrue(state['ready'])
                self.assertEqual(state['accounts'][provider]['status'],'Linked')
                other='trucky' if provider=='trucksbook' else 'trucksbook'
                self.assertEqual(state['accounts'][other]['status'],'Not Linked')
                self.assertEqual(dict(await self.conn.fetchrow('SELECT * FROM driver_progress WHERE discord_user_id=$1',user)),before_progress)
                self.assertEqual(dict(await self.conn.fetchrow('SELECT * FROM processed_jobs WHERE job_id=$1',job)),before_job)
                self.assertEqual(await self.conn.fetchval('SELECT COUNT(*) FROM unified_provider_job_events WHERE discord_user_id=$1',user),0)


if __name__ == "__main__":
    unittest.main()


