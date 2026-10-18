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
            resolved = await review.resolve_review_case(
                self.pool, "8877665531", "8877665532",
                "confirmed_duplicate", user, "independent_telemetry", digest,
            )
        self.assertEqual(resolved["status"], "confirmed_duplicate")
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
        self.assertEqual(status, "confirmed_duplicate")
        self.assertEqual(audit["decision"], "confirmed_duplicate")
        self.assertEqual(audit["evidence_sha256"], digest)
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


if __name__ == "__main__":
    unittest.main()
