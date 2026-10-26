"""Production-adapter rehearsal using localhost/v2_test disposable PostgreSQL only."""
import asyncio
import os
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from decimal import Decimal

try:
    import asyncpg
except ImportError:
    asyncpg = None

from unified_driver_profiles import SCHEMA as PROFILE_SCHEMA, record_normalized_provider_job
from unified_production_ledger_adapter_v2 import (
    SCHEMA as LEDGER_SCHEMA,
    apply_verified_credit,
    install_production_ledger_schema,
)
from unified_production_mileage_gate_v2 import (
    PRODUCTION_ENVIRONMENT_ID,
    PRODUCTION_SERVICE_ID,
    read_only_database_fingerprint,
)
from unified_mileage_engine_control_v2 import canonical_credit_key


@unittest.skipUnless(
    asyncpg is not None
    and os.getenv("AT_V2_TEST_DATABASE_DISPOSABLE") == "YES"
    and "/v2_test" in os.getenv("TEST_DATABASE_URL", ""),
    "Requires disposable v2_test PostgreSQL",
)
class ProductionLedgerAdapterDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from urllib.parse import urlsplit
        parsed = urlsplit(os.environ["TEST_DATABASE_URL"])
        if (parsed.hostname not in ("localhost", "127.0.0.1", "::1")
                or parsed.path != "/v2_test"):
            self.skipTest("Production adapter integration tests are localhost/v2_test only")
        self.pool = await asyncpg.create_pool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4)
        async with self.pool.acquire() as conn:
            await conn.execute(PROFILE_SCHEMA)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS driver_progress (
                  discord_user_id BIGINT PRIMARY KEY,
                  trucksbook_name TEXT NOT NULL UNIQUE,
                  real_miles BIGINT NOT NULL DEFAULT 0,
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
                CREATE TABLE IF NOT EXISTS processed_jobs (
                  job_id TEXT PRIMARY KEY, discord_user_id BIGINT NOT NULL,
                  trucksbook_name TEXT NOT NULL DEFAULT '',
                  accepted_distance INTEGER NOT NULL DEFAULT 0,
                  statistics TEXT NOT NULL DEFAULT '',
                  processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
            """)
        self.config, self.runtime = await self.approved_test_boundary()
        injected = {
            **self.config,
            **{key: self.runtime[key] for key in (
                "RAILWAY_ENVIRONMENT_ID", "RAILWAY_SERVICE_ID",
                "RAILWAY_DEPLOYMENT_ID", "RAILWAY_GIT_COMMIT_SHA",
            )},
            "AT_V2_ISOLATED_STAGING": "",
            "AT_V2_TEST_DATABASE_DISPOSABLE": "",
            "AT_V2_MILEAGE_ENGINE_TEST_ENABLED": "",
        }
        patcher = patch.dict(os.environ, injected, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.pool.close()

    async def approved_test_boundary(self):
        async with self.pool.acquire() as conn:
            fingerprint = await read_only_database_fingerprint(conn)
        commit = "c" * 40
        deployment = "isolated-disposable-test-deployment"
        preflight = "b" * 64
        return ({
            "AT_V2_PRODUCTION_MILEAGE_ENABLED": "YES",
            "AT_V2_PRODUCTION_OWNER_APPROVAL_REF": "isolated-test-fixture-only",
            "AT_V2_APPROVED_DEPLOYMENT_ID": deployment,
            "AT_V2_APPROVED_RELEASE_COMMIT": commit,
            "AT_V2_EXPECTED_DATABASE_FINGERPRINT": fingerprint,
            "AT_V2_APPROVED_PREFLIGHT_SHA256": preflight,
        }, {
            "RAILWAY_ENVIRONMENT_ID": PRODUCTION_ENVIRONMENT_ID,
            "RAILWAY_SERVICE_ID": PRODUCTION_SERVICE_ID,
            "RAILWAY_DEPLOYMENT_ID": deployment,
            "RAILWAY_GIT_COMMIT_SHA": commit,
            "source_authentication_verified": True,
            "preflight_acceptance_passed": True,
            "preflight_sha256": preflight,
            "legacy_writer_drained": True,
            "historical_overlap_audit_passed": True,
            "backup_restore_rollback_rehearsal_passed": True,
        })

    async def seed_verified_driver_job(self, user, provider, job_id, account, miles, kind="real"):
        steam_id = str(76561190000000000 + user % 100000000)
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO unified_driver_profiles(discord_user_id) VALUES($1)
                ON CONFLICT DO NOTHING
            """, user)
            await conn.execute("""
                INSERT INTO unified_steam_links(discord_user_id,steam_id64,verified_at)
                VALUES($1,$2,NOW()) ON CONFLICT(discord_user_id) DO NOTHING
            """, user, steam_id)
            await conn.execute("""
                INSERT INTO unified_provider_links
                  (provider,provider_account_id,discord_user_id,verified_at)
                VALUES($1,$2,$3,NOW()) ON CONFLICT DO NOTHING
            """, provider, account, user)
            await conn.execute("""
                INSERT INTO unified_provider_claims
                  (discord_user_id,provider,provider_account_id,membership_confirmed,
                   ownership_confirmed,status,reviewed_by,reviewed_at,
                   ownership_evidence_kind,ownership_evidence_sha256)
                VALUES($1,$2,$3,TRUE,TRUE,'approved',777,NOW(),$4,$5)
                ON CONFLICT DO NOTHING
            """, user, provider, account,
                "trucky_signed_job_steam_match" if provider == "trucky"
                else "trucksbook_steam_authenticated_session", "d" * 64)
        await record_normalized_provider_job(
            self.pool, provider, job_id, user, str(miles), "mi",
            provider_account_id=account, mileage_type=kind,
            delivered_at=datetime(2026, 10, 19, tzinfo=timezone.utc),
        )
        canonical = canonical_credit_key(provider, job_id)
        evidence_sha = "a" * 64
        job = {
            "provider": provider,
            "provider_job_id": job_id,
            "provider_account_id": account,
            "discord_user_id": user,
            "miles": str(miles),
            "mileage_type": kind,
            "provider_authenticated": True,
            "completed": True,
            "job_status": "completed",
            "ownership_verified": True,
            "steam_identity_verified": True,
            "ownership_evidence_kind": (
                "trucky_signed_job_steam_match" if provider == "trucky"
                else "trucksbook_steam_authenticated_session"
            ),
            "source_evidence_sha256": evidence_sha,
            "ownership_evidence_sha256": "d" * 64,
            "delivered_at": datetime(2026, 10, 19, tzinfo=timezone.utc),
            "canonical_credit_key": canonical,
        }
        return job

    def complete_feed(self):
        return {
            "authenticated": True,
            "complete": True,
            "watermark": datetime(2026, 10, 19, tzinfo=timezone.utc),
            "snapshot_sha256": "e" * 64,
        }

    async def install_ledger(self):
        return await install_production_ledger_schema(
            self.pool, config=self.config, runtime=self.runtime,
        )

    async def test_migration_refuses_without_explicit_production_preflight(self):
        async with self.pool.acquire() as conn:
            before_count = await conn.fetchval("""
                SELECT count(*) FROM information_schema.tables
                WHERE table_schema=current_schema()
                  AND table_name LIKE 'unified_v2_production_%'
            """)
        with self.assertRaisesRegex(RuntimeError, "Production ledger migration blocked"):
            await install_production_ledger_schema(self.pool, config={}, runtime={})
        async with self.pool.acquire() as conn:
            after_count = await conn.fetchval("""
                SELECT count(*) FROM information_schema.tables
                WHERE table_schema=current_schema()
                  AND table_name LIKE 'unified_v2_production_%'
            """)
        self.assertEqual(after_count, before_count)

    async def award(self, job, **kwargs):
        return await apply_verified_credit(
            self.pool, job, config=self.config, runtime=self.runtime,
            feed_evidence=self.complete_feed(), reconciliation_status="no_candidate",
            **kwargs,
        )

    async def test_additive_migration_and_interrupted_install_preserve_legacy_rows(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS driver_progress (
                  discord_user_id BIGINT PRIMARY KEY, trucksbook_name TEXT NOT NULL,
                  real_miles BIGINT NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS processed_jobs (
                  job_id TEXT PRIMARY KEY, discord_user_id BIGINT NOT NULL);
                INSERT INTO driver_progress(discord_user_id,trucksbook_name,real_miles)
                  VALUES(991234567890123701,'preserved',40000)
                  ON CONFLICT DO NOTHING;
                INSERT INTO processed_jobs(job_id,discord_user_id)
                  VALUES('historical-991234567890123701',991234567890123701)
                  ON CONFLICT DO NOTHING;
            """)
            with self.assertRaises(RuntimeError):
                async with conn.transaction():
                    await conn.execute(LEDGER_SCHEMA)
                    raise RuntimeError("simulated interrupted migration")
            tables_after_rollback = await conn.fetchval("""
                SELECT count(*) FROM information_schema.tables
                WHERE table_schema=current_schema() AND table_name LIKE 'unified_v2_production_%'
            """)
            self.assertEqual(tables_after_rollback, 0)
            self.assertEqual(await conn.fetchval("SELECT real_miles FROM driver_progress WHERE discord_user_id=991234567890123701"), 40000)
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM processed_jobs WHERE job_id='historical-991234567890123701'"), 1)
        await self.install_ledger()
        await self.install_ledger()
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval("SELECT real_miles FROM driver_progress WHERE discord_user_id=991234567890123701"), 40000)
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM processed_jobs WHERE job_id='historical-991234567890123701'"), 1)
            self.assertEqual(await conn.fetchval("SELECT trucksbook_real_miles FROM unified_v2_production_mileage_baselines WHERE discord_user_id=991234567890123701"), 40000)

    async def test_provider_retries_fractionals_and_policy_buckets(self):
        await self.install_ledger()
        tb = await self.seed_verified_driver_job(991234567890123710, "trucky", "991234567890123711", "991234567890123714", "40.12")
        tkr = await self.seed_verified_driver_job(991234567890123710, "trucky", "991234567890123713", "991234567890123714", "60.37", "race")
        self.assertEqual((await self.award(tb))["status"], "credited")
        self.assertEqual((await self.award(tkr))["status"], "credited")
        self.assertEqual((await self.award(tb))["status"], "duplicate_provider_job")
        async with self.pool.acquire() as conn:
            totals = await conn.fetchrow("SELECT * FROM unified_v2_production_mileage_totals WHERE discord_user_id=$1", tb["discord_user_id"])
            legacy = await conn.fetchval("SELECT real_miles FROM driver_progress WHERE discord_user_id=$1", tb["discord_user_id"])
        self.assertEqual(totals["combined_real_miles"], Decimal("40.12"))
        self.assertEqual(totals["trucky_race_miles"], Decimal("60.37"))
        self.assertEqual(totals["trucksbook_real_miles"], Decimal("0"))
        self.assertIsNone(legacy)

    async def test_ambiguous_cross_provider_pair_is_audited_without_credit(self):
        await self.install_ledger()
        job = await self.seed_verified_driver_job(991234567890123720, "trucky", "991234567890123721", "991234567890123722", "176")
        result = await apply_verified_credit(
            self.pool, job, config=self.config, runtime=self.runtime,
            feed_evidence=self.complete_feed(), reconciliation_status="pending",
        )
        self.assertEqual(result["status"], "pending_reconciliation")
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM unified_v2_production_credit_ledger WHERE provider_job_id=$1", job["provider_job_id"]), 0)
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM unified_v2_production_mileage_decisions WHERE provider_job_id=$1 AND decision='pending_reconciliation'", job["provider_job_id"]), 1)
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM unified_v2_production_mileage_totals WHERE discord_user_id=$1", job["discord_user_id"]), 0)

    async def test_historical_trucksbook_job_is_held_without_changing_totals(self):
        job = await self.seed_verified_driver_job(991234567890123725, "trucksbook", "991234567890123726", "991234567890123727", "88")
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO driver_progress(discord_user_id,trucksbook_name,real_miles)
                VALUES($1,'historical-preserved',12345) ON CONFLICT DO NOTHING
            """, job["discord_user_id"])
            await conn.execute("""
                INSERT INTO processed_jobs(job_id,discord_user_id,trucksbook_name,accepted_distance,statistics)
                VALUES($1,$2,'historical-preserved',88,'real') ON CONFLICT DO NOTHING
            """, job["provider_job_id"], job["discord_user_id"])
        await self.install_ledger()
        result = await self.award(job)
        self.assertEqual(result["status"], "rejected_evidence")
        self.assertEqual(result["reason"], "ownership_evidence_kind_unverified")
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval("SELECT real_miles FROM driver_progress WHERE discord_user_id=$1", job["discord_user_id"]), 12345)
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM unified_v2_production_credit_ledger WHERE provider_job_id=$1", job["provider_job_id"]), 0)
            self.assertEqual(await conn.fetchval("SELECT trucksbook_real_miles FROM unified_v2_production_mileage_baselines WHERE discord_user_id=$1", job["discord_user_id"]), 12345)

    async def test_decimal_miles_accumulate_without_whole_mile_rounding(self):
        await self.install_ledger()
        user = 991234567890123728
        first = await self.seed_verified_driver_job(user, "trucky", "991234567890123729", "991234567890123730", "0.37")
        second = await self.seed_verified_driver_job(user, "trucky", "991234567890123735", "991234567890123730", "0.63")
        self.assertEqual((await self.award(first))["status"], "credited")
        self.assertEqual((await self.award(second))["status"], "credited")
        async with self.pool.acquire() as conn:
            total = await conn.fetchval("SELECT combined_real_miles FROM unified_v2_production_mileage_totals WHERE discord_user_id=$1", user)
        self.assertEqual(total, Decimal("1.00"))

    async def test_retired_trucksbook_evidence_cannot_confirm_duplicate_credit(self):
        await self.install_ledger()
        user = 991234567890123730
        first = await self.seed_verified_driver_job(user, "trucksbook", "991234567890123731", "991234567890123732", "176")
        second = await self.seed_verified_driver_job(user, "trucky", "991234567890123733", "991234567890123734", "176")
        shared_key = canonical_credit_key("trucky", second["provider_job_id"],
                                          shared_journey_id="reviewed-delivery-1",
                                          shared_journey_evidence_sha256="f" * 64)
        first["canonical_credit_key"] = shared_key
        second["canonical_credit_key"] = shared_key
        first["reconciliation_primary"] = {
            "provider": "trucksbook", "provider_job_id": first["provider_job_id"]
        }
        second["reconciliation_primary"] = first["reconciliation_primary"]
        first["reconciliation_companion"] = {
            "provider": "trucky", "provider_job_id": second["provider_job_id"]
        }
        second["reconciliation_companion"] = {
            "provider": "trucksbook", "provider_job_id": first["provider_job_id"]
        }
        async with self.pool.acquire() as conn:
            case_id = await conn.fetchval("""
                INSERT INTO unified_job_reconciliation_cases
                  (trucky_job_id,trucksbook_job_id,discord_user_id,status,opened_by,reviewed_at)
                VALUES($1,$2,$3,'confirmed_duplicate',777,NOW()) RETURNING case_id
            """, second["provider_job_id"], first["provider_job_id"], user)
            await conn.execute("""
                INSERT INTO unified_job_reconciliation_audit
                  (case_id,decision,reviewer_discord_id,evidence_kind,evidence_sha256,discord_user_id)
                VALUES($1,'confirmed_duplicate',777,'provider_shared_journey_evidence',$2,$3)
            """, case_id, "f" * 64, user)
        async def award_primary(job):
            return await apply_verified_credit(
                self.pool, job, config=self.config, runtime=self.runtime,
                feed_evidence=self.complete_feed(), reconciliation_status="confirmed_duplicate",
                reconciliation_evidence_sha256="f" * 64, reviewer_id=777,
            )
        # Concurrent replay of the selected primary produces one credit.
        results = await asyncio.gather(award_primary(first), award_primary(first))
        self.assertEqual(sorted(result["status"] for result in results), ["rejected_evidence", "rejected_evidence"])
        companion = await apply_verified_credit(
            self.pool, second, config=self.config, runtime=self.runtime,
            feed_evidence=self.complete_feed(), reconciliation_status="confirmed_duplicate",
            reconciliation_evidence_sha256="f" * 64, reviewer_id=777,
        )
        self.assertEqual(companion["status"], "pending_reconciliation")
        self.assertEqual(companion["reason"], "duplicate_not_selected_for_credit")
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM unified_v2_production_mileage_totals WHERE discord_user_id=$1", user), 0)
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM unified_v2_production_credit_ledger WHERE discord_user_id=$1", user), 0)
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM unified_v2_production_mileage_decisions WHERE provider_job_id=$1 AND decision='pending_reconciliation'", second["provider_job_id"]), 1)

    async def test_partial_failure_rolls_back_ledger_and_total(self):
        await self.install_ledger()
        job = await self.seed_verified_driver_job(991234567890123740, "trucky", "991234567890123741", "991234567890123742", "100")
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE OR REPLACE FUNCTION test_fail_production_total_insert()
                RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
                  IF NEW.discord_user_id=991234567890123740 THEN
                    RAISE EXCEPTION 'injected isolated transaction failure';
                  END IF;
                  RETURN NEW;
                END $$;
                DROP TRIGGER IF EXISTS test_fail_production_total_insert
                  ON unified_v2_production_mileage_totals;
                CREATE TRIGGER test_fail_production_total_insert
                  BEFORE INSERT ON unified_v2_production_mileage_totals
                  FOR EACH ROW EXECUTE FUNCTION test_fail_production_total_insert();
            """)
        with self.assertRaises(asyncpg.PostgresError):
            await self.award(job)
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM unified_v2_production_credit_ledger WHERE provider_job_id=$1", job["provider_job_id"]), 0)
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM unified_v2_production_mileage_totals WHERE discord_user_id=$1", job["discord_user_id"]), 0)

    async def test_gate_refuses_staging_and_test_overrides(self):
        await self.install_ledger()
        job = await self.seed_verified_driver_job(991234567890123750, "trucky", "991234567890123751", "991234567890123752", "1")
        result = await apply_verified_credit(
            self.pool, job, config={**self.config, "AT_V2_ISOLATED_STAGING": "YES"},
            runtime=self.runtime, feed_evidence=self.complete_feed(),
            reconciliation_status="no_candidate",
        )
        self.assertEqual(result["status"], "activation_blocked")
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM unified_v2_production_credit_ledger WHERE provider_job_id=$1", job["provider_job_id"]), 0)
        with patch.dict(os.environ, {"AT_V2_TEST_DATABASE_DISPOSABLE": "YES"}):
            result = await apply_verified_credit(
                self.pool, job, config=self.config, runtime=self.runtime,
                feed_evidence=self.complete_feed(), reconciliation_status="no_candidate",
            )
        self.assertEqual(result["status"], "activation_blocked")


    async def test_committed_progression_outbox_retries_without_recrediting(self):
        from unittest.mock import AsyncMock
        from unified_production_ledger_adapter_v2 import deliver_pending_progression
        await self.install_ledger()
        # Isolate this disposable outbox fixture from credits in other tests.
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE unified_v2_production_mileage_decisions SET progression_delivered_at=NOW() WHERE decision='credited'")
        user = 991234567890125501
        job = await self.seed_verified_driver_job(user, 'trucky', '998825501', '8825501', '40000.125')
        self.assertEqual((await self.award(job))['status'], 'credited')
        config = {**self.config, 'AT_V2_PROGRESSION_ENABLED':'YES', 'AT_V2_COMPANY_MODS_ENABLED':'YES'}
        callback = AsyncMock(side_effect=RuntimeError('Discord temporarily unavailable'))
        with self.assertRaises(RuntimeError):
            await deliver_pending_progression(self.pool,callback,config=config,runtime=self.runtime)
        async with self.pool.acquire() as conn:
            self.assertIsNone(await conn.fetchval("SELECT progression_delivered_at FROM unified_v2_production_mileage_decisions WHERE provider_job_id=$1 AND decision='credited'",job['provider_job_id']))
            before = dict(await conn.fetchrow('SELECT * FROM unified_v2_production_mileage_totals WHERE discord_user_id=$1',user))
        callback = AsyncMock(return_value={'status':'correct'})
        results = await asyncio.gather(*[deliver_pending_progression(self.pool,callback,config=config,runtime=self.runtime) for _ in range(2)])
        self.assertEqual(sum(r['delivered'] for r in results),1)
        callback.assert_awaited_once_with(user,Decimal('40000.125'),True)
        async with self.pool.acquire() as conn:
            self.assertEqual(before,dict(await conn.fetchrow('SELECT * FROM unified_v2_production_mileage_totals WHERE discord_user_id=$1',user)))
        callback.reset_mock()
        self.assertEqual((await deliver_pending_progression(self.pool,callback,config=config,runtime=self.runtime))['delivered'],0)
        callback.assert_not_awaited()


    async def test_canonical_constraint_prevents_double_real_credit_in_either_order(self):
        # Constraint-level disposable fixture: this does not invent provider
        # ownership or bypass the separately tested application evidence gates.
        await self.install_ledger()
        for offset, order in enumerate((('trucky','trucksbook'),('trucksbook','trucky'))):
            user=991234567890126101+offset
            key='v2:'+str(offset+1)*64
            async with self.pool.acquire() as conn:
                await conn.execute('INSERT INTO unified_driver_profiles(discord_user_id) VALUES($1)',user)
                async with conn.transaction():
                    for provider in order:
                        await conn.execute("""INSERT INTO unified_v2_production_credit_ledger
                            (provider,provider_job_id,discord_user_id,provider_account_id,mileage_type,miles,
                             canonical_credit_key,source_evidence_sha256,ownership_evidence_sha256)
                            VALUES($1,$2,$3,'123','real',176,$4,$5,$5) ON CONFLICT DO NOTHING""",
                            provider,str(user),user,key,'a'*64)
                self.assertEqual(await conn.fetchval('SELECT SUM(miles) FROM unified_v2_production_credit_ledger WHERE discord_user_id=$1',user),Decimal('176'))
                self.assertEqual(await conn.fetchval('SELECT COUNT(*) FROM unified_v2_production_credit_ledger WHERE discord_user_id=$1',user),1)


if __name__ == "__main__":
    unittest.main()


