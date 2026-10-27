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

    async def test_pre_registration_history_survives_linking_replays_and_conflicts(self):
        from pending_driver_history_v2 import install_schema, record_pending_history, linked_history_summary
        await install_schema(self.pool)
        user=991234567890124200
        evidence={'status':'completed','driven_distance_km':586}
        async def retain():
            return await record_pending_history(self.pool,'trucky','991234567890124201','991234567890124202',evidence,source_kind='authenticated_api')
        await asyncio.gather(retain(),retain())
        self.assertEqual((await linked_history_summary(self.pool,user))['jobs'],0)
        await self.seed_verified_driver_job(user,'trucky','991234567890124203','991234567890124202','1')
        summary=await linked_history_summary(self.pool,user)
        self.assertEqual(summary['jobs'],1)
        self.assertEqual(summary['replays'],1)
        self.assertEqual(summary['ownership_verified_jobs'],1)
        self.assertEqual(summary['conflicting_jobs'],0)
        self.assertEqual(summary['ready_to_award'],0)
        await record_pending_history(self.pool,'trucky','991234567890124201','991234567890124202',{'status':'completed','driven_distance_km':635},source_kind='authenticated_api')
        summary=await linked_history_summary(self.pool,user)
        self.assertEqual(summary['jobs'],1)
        self.assertEqual(summary['conflicting_jobs'],1)
        self.assertEqual(summary['ownership_verified_jobs'],0)
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM pending_provider_job_history_v2 WHERE provider_job_id='991234567890124201'"),2)
            self.assertIsNone(await conn.fetchval('SELECT real_miles FROM driver_progress WHERE discord_user_id=$1',user))
        self.assertFalse(summary['mileage_awarded'])

    async def test_saved_history_expired_claim_and_changed_account_are_not_release_ready(self):
        from pending_driver_history_v2 import install_schema, record_pending_history, linked_history_summary
        await install_schema(self.pool)
        user=991234567890124210
        account='991234567890124212'
        await self.seed_verified_driver_job(user,'trucky','991234567890124213',account,'1')
        evidence={'status':'completed','driven_distance_km':100}
        for job in ('991234567890124214','991234567890124215'):
            await record_pending_history(self.pool,'trucky',job,account,evidence,source_kind='authenticated_api')
        await record_pending_history(self.pool,'trucky','991234567890124214','991234567890124299',evidence,source_kind='authenticated_api')
        await record_pending_history(self.pool,'trucky','991234567890124215',None,{'candidate_driver_label':'untrusted'},source_kind='owner_export')
        summary=await linked_history_summary(self.pool,user)
        self.assertEqual(summary['ownership_verified_jobs'],1)
        self.assertEqual(summary['conflicting_jobs'],1)
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE unified_provider_claims SET expires_at=NOW()-INTERVAL '1 minute' WHERE discord_user_id=$1",user)
            before=[dict(r) for r in await conn.fetch('SELECT * FROM unified_provider_links WHERE discord_user_id=$1',user)]
        summary=await linked_history_summary(self.pool,user)
        self.assertEqual(summary['jobs'],2)
        self.assertEqual(summary['ownership_verified_jobs'],0)
        self.assertEqual(summary['ownership_pending_jobs'],1)
        self.assertEqual(summary['conflicting_jobs'],1)
        self.assertEqual(summary['ready_to_award'],0)
        async with self.pool.acquire() as conn:
            self.assertEqual(before,[dict(r) for r in await conn.fetch('SELECT * FROM unified_provider_links WHERE discord_user_id=$1',user)])
            self.assertIsNone(await conn.fetchval('SELECT real_miles FROM driver_progress WHERE discord_user_id=$1',user))

    async def test_history_sweep_checkpoint_restart_replay_and_failed_page(self):
        from unittest.mock import AsyncMock,patch
        from pending_driver_history_v2 import install_schema,record_pending_history
        import trucky_jobs_api_v2 as api
        await install_schema(self.pool)
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM pending_history_capture_cursor_v2")
        records=[{'id':991234567890124300,'user_id':991234567890124301,'company_id':49041,
                  'status':'completed','driven_distance_km':100}]
        async def capture(pool,rows):
            for row in rows:
                await record_pending_history(pool,'trucky',str(row['id']),str(row['user_id']),
                    {'status':'completed','driven_distance_km':row['driven_distance_km']},source_kind='authenticated_api')
            return {'counts':{'history_retained':len(rows)}}
        env={'AT_V2_PENDING_HISTORY_ENABLED':'YES','AT_V2_ISOLATED_STAGING':'NO'}
        with patch.dict(os.environ,env),patch.object(api,'fetch_trucky_jobs_page',AsyncMock(return_value={'data':records})) as fetch,patch.object(api,'_ingest_authenticated_job_records',side_effect=capture):
            result=await api.sweep_saved_trucky_history(self.pool)
            self.assertEqual(result['status'],'history_page_processed')
            fetch.assert_awaited_with(2)
            self.assertFalse(result['mileage_awarded'])
            self.assertFalse(result['feed_complete'])
            self.assertEqual((await api.sweep_saved_trucky_history(self.pool))['status'],'repeated_page_pending')
            fetch.assert_awaited_with(3)
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval("SELECT next_page FROM pending_history_capture_cursor_v2"),3)
        async def failed_capture(pool,rows):
            await capture(pool,rows)
            raise RuntimeError('interrupted after evidence commit')
        next_records=[{**records[0],'id':991234567890124302}]
        with patch.dict(os.environ,env),patch.object(api,'fetch_trucky_jobs_page',AsyncMock(return_value=next_records)),patch.object(api,'_ingest_authenticated_job_records',side_effect=failed_capture):
            with self.assertRaises(RuntimeError):await api.sweep_saved_trucky_history(self.pool)
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval("SELECT next_page FROM pending_history_capture_cursor_v2"),3)
        with patch.dict(os.environ,env),patch.object(api,'fetch_trucky_jobs_page',AsyncMock(return_value=next_records)),patch.object(api,'_ingest_authenticated_job_records',side_effect=capture):
            self.assertEqual((await api.sweep_saved_trucky_history(self.pool))['page'],3)
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval("SELECT replay_count FROM pending_provider_job_history_v2 WHERE provider_job_id='991234567890124302'"),1)
            await conn.execute("UPDATE pending_history_capture_cursor_v2 SET next_page=100,previous_page_sha256=NULL")
        with patch.dict(os.environ,env),patch.object(api,'fetch_trucky_jobs_page',AsyncMock(return_value=next_records)),patch.object(api,'_ingest_authenticated_job_records',side_effect=capture):
            limit=await api.sweep_saved_trucky_history(self.pool)
            self.assertEqual(limit['status'],'page_limit_pending')
            self.assertFalse(limit['feed_complete'])
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval('SELECT next_page FROM pending_history_capture_cursor_v2'),100)
        with patch.dict(os.environ,env),patch.object(api,'fetch_trucky_jobs_page',AsyncMock(return_value=[])):
            self.assertEqual((await api.sweep_saved_trucky_history(self.pool))['status'],'sweep_end_observed')
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval("SELECT next_page FROM pending_history_capture_cursor_v2"),2)
            self.assertEqual(await conn.fetchval("SELECT completed_sweeps FROM pending_history_capture_cursor_v2"),1)

    async def test_history_sweep_concurrent_workers_and_invalid_schema_hold_checkpoint(self):
        from unittest.mock import AsyncMock,patch
        from pending_driver_history_v2 import install_schema
        import trucky_jobs_api_v2 as api
        await install_schema(self.pool)
        async with self.pool.acquire() as conn:await conn.execute('DELETE FROM pending_history_capture_cursor_v2')
        entered=asyncio.Event();release=asyncio.Event()
        async def fetch(page):
            entered.set();await release.wait();return {'unexpected':[]}
        with patch.dict(os.environ,{'AT_V2_PENDING_HISTORY_ENABLED':'YES','AT_V2_ISOLATED_STAGING':'NO'}),patch.object(api,'fetch_trucky_jobs_page',side_effect=fetch):
            worker=asyncio.create_task(api.sweep_saved_trucky_history(self.pool))
            await asyncio.wait_for(entered.wait(),5)
            try:self.assertEqual((await api.sweep_saved_trucky_history(self.pool))['status'],'worker_busy')
            finally:release.set()
            with self.assertRaises(api.TruckyJobLookupUnavailable):await worker
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval('SELECT COUNT(*) FROM pending_history_capture_cursor_v2'),0)

    async def test_pending_race_inventory_rechecks_claims_without_touching_totals(self):
        from pending_driver_history_v2 import install_schema,record_pending_history,linked_history_summary
        from pending_race_mileage_v2 import read_pending_race
        from unified_distance_units import KM_TO_MILES
        await install_schema(self.pool)
        user,account=991234567890128001,'8828001'
        await self.seed_verified_driver_job(user,'trucky','8828002',account,'1')
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE unified_driver_profiles SET status='active' WHERE discord_user_id=$1",user)
        evidence=dict(game_id=2,status='completed',stats_type='race_miles',distance_unit='km',
            driven_distance_km=100,completed_at='2026-10-19T12:00:00Z')
        await record_pending_history(self.pool,'trucky','8828003',account,evidence,source_kind='authenticated_api')
        await record_pending_history(self.pool,'trucky','8828003',account,{**evidence,'max_speed_kmh':132},source_kind='authenticated_api')
        result=await read_pending_race(self.pool,user)
        self.assertEqual(result['verified_pending_miles'],100*KM_TO_MILES)
        self.assertFalse(result['mileage_awarded'])
        summary=await linked_history_summary(self.pool,user)
        self.assertEqual(summary['ownership_verified_jobs'],1)
        self.assertEqual(summary['conflicting_jobs'],0)
        async with self.pool.acquire() as conn:
            self.assertIsNone(await conn.fetchval('SELECT real_miles FROM driver_progress WHERE discord_user_id=$1',user))
            await conn.execute("UPDATE unified_provider_claims SET expires_at=NOW()-INTERVAL '1 minute' WHERE discord_user_id=$1",user)
        result=await read_pending_race(self.pool,user)
        self.assertEqual(result['verified_pending_miles'],0)
        self.assertEqual(result['unverified_miles'],100*KM_TO_MILES)

    async def test_historical_preview_reads_consistent_database_without_writes(self):
        from historical_mileage_preview_v2 import read_historical_snapshot, build_historical_preview
        user = 991234567890124100
        await self.seed_verified_driver_job(user, "trucky", "991234567890124101", "991234567890124102", "1")
        async with self.pool.acquire() as conn:
            await conn.execute("INSERT INTO driver_progress(discord_user_id,trucksbook_name,real_miles) VALUES($1,'preview-preserved',88942)", user)
            await conn.execute("INSERT INTO processed_jobs(job_id,discord_user_id,accepted_distance,statistics) VALUES('991234567890124103',$1,365,'Real')", user)
        before = await read_historical_snapshot(self.pool)
        report = build_historical_preview(before, [])
        after = await read_historical_snapshot(self.pool)
        for key in ("drivers", "processed_jobs", "links"):
            self.assertEqual(before[key], after[key])
        driver = next(r for r in report['drivers'] if r['discord_user_id']==user)
        self.assertEqual(driver['proposed_real_total'], '88942')
        self.assertEqual(driver['unexplained_balance'], '88577')
        self.assertFalse(report['award_authorized'])

    async def test_existing_review_cannot_be_bypassed_as_no_candidate(self):
        await self.install_ledger()
        for index, status in enumerate(("potential_duplicate", "confirmed_duplicate", "verified_independent")):
            user = 991234567890124000 + index
            job = await self.seed_verified_driver_job(user, "trucky", str(991234567890124010 + index), str(991234567890124020 + index), "364.1235186511")
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO unified_job_reconciliation_cases
                      (trucky_job_id,trucksbook_job_id,discord_user_id,status,opened_by)
                    VALUES($1,$2,$3,$4,777)
                """, job["provider_job_id"], str(991234567890124030 + index), user, status)
            results = await asyncio.gather(self.award(job), self.award(job))
            expected = {"status":"pending_reconciliation", "reason":"existing_case_requires_audited_review"}
            self.assertEqual(results, [expected, expected])
            async with self.pool.acquire() as conn:
                self.assertEqual(await conn.fetchval("SELECT count(*) FROM unified_v2_production_credit_ledger WHERE discord_user_id=$1", user), 0)
                self.assertEqual(await conn.fetchval("SELECT count(*) FROM unified_v2_production_mileage_totals WHERE discord_user_id=$1", user), 0)
                self.assertEqual(await conn.fetchval("SELECT status FROM unified_job_reconciliation_cases WHERE trucky_job_id=$1", job["provider_job_id"]), status)

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

    async def test_reviewed_trucky_primary_cannot_recredit_historical_trucksbook(self):
        user = 991234567890123881
        job = await self.seed_verified_driver_job(user, "trucky", "991234567890123883", "991234567890123882", "364.1235186511")
        historical_id = "991234567890123884"
        async with self.pool.acquire() as conn:
            await conn.execute("INSERT INTO driver_progress(discord_user_id,trucksbook_name,real_miles) VALUES($1,'legacy-duplicate-test',88942)", user)
            await conn.execute("INSERT INTO processed_jobs(job_id,discord_user_id,accepted_distance,statistics) VALUES($1,$2,365,'real')", historical_id, user)
            case_id = await conn.fetchval("""
                INSERT INTO unified_job_reconciliation_cases
                  (trucky_job_id,trucksbook_job_id,discord_user_id,status,opened_by,reviewed_at)
                VALUES($1,$2,$3,'confirmed_duplicate',777,NOW()) RETURNING case_id
            """, job["provider_job_id"], historical_id, user)
            await conn.execute("""
                INSERT INTO unified_job_reconciliation_audit
                  (case_id,decision,reviewer_discord_id,evidence_kind,evidence_sha256,discord_user_id)
                VALUES($1,'confirmed_duplicate',777,'provider_shared_journey_evidence',$2,$3)
            """, case_id, "f" * 64, user)
        await self.install_ledger()
        job["reconciliation_primary"] = {"provider":"trucky", "provider_job_id":job["provider_job_id"]}
        job["reconciliation_companion"] = {"provider":"trucksbook", "provider_job_id":historical_id}
        async def attempt():
            return await apply_verified_credit(self.pool, job, config=self.config, runtime=self.runtime,
                feed_evidence=self.complete_feed(), reconciliation_status="confirmed_duplicate",
                reconciliation_evidence_sha256="f" * 64, reviewer_id=777)
        results = await asyncio.gather(attempt(), attempt())
        self.assertTrue(all(r == {"status":"pending_reconciliation", "reason":"duplicate_already_in_historical_baseline"} for r in results))
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval("SELECT real_miles FROM driver_progress WHERE discord_user_id=$1", user),88942)
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM unified_v2_production_credit_ledger WHERE discord_user_id=$1", user),0)
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM unified_v2_production_mileage_decisions WHERE discord_user_id=$1 AND reason_code='duplicate_already_in_historical_baseline'", user),2)

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


    async def test_signed_ats_jobs_use_company_speed_policy_and_preserve_conflicting_replays(self):
        import hashlib,json
        from unittest.mock import AsyncMock
        from unified_driver_profiles import record_trucky_webhook_receipt,attest_trucky_receipt_identity
        import trucky_jobs_api_v2 as api
        await self.install_ledger()
        user,account=991234567890127201,'8827201'
        await self.seed_verified_driver_job(user,'trucky','8827200',account,'1')
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE unified_driver_profiles SET status='active' WHERE discord_user_id=$1",user)
        records=[]
        for job_id,speed,stats in [('8827202',129,'race_miles'),('8827203',132,'real_miles')]:
            digest=hashlib.sha256(('signed-ats-'+job_id).encode()).hexdigest()
            receipt=await record_trucky_webhook_receipt(self.pool,digest,'job_completed',job_id)
            raw_id=receipt['receipt_id'] if isinstance(receipt,dict) else receipt
            self.assertTrue(await attest_trucky_receipt_identity(self.pool,raw_id,job_id,digest,user,account))
            records.append(dict(id=int(job_id),user_id=int(account),company_id=49041,status='completed',
                game_id=2,game={'id':2,'code':'ATS'},max_speed_kmh=speed,
                completed_at='2026-10-19T12:00:00Z',stats_type=stats,distance_unit='km',driven_distance_km=100))
        with patch.object(api,'fetch_trucky_jobs_page',AsyncMock(return_value={'data':records})):
            self.assertEqual((await api.ingest_authenticated_completed_jobs(self.pool))['counts']['recorded'],2)
            self.assertEqual((await api.ingest_authenticated_completed_jobs(self.pool))['counts']['replayed'],2)
        async with self.pool.acquire() as conn:
            rows=await conn.fetch("SELECT provider_job_id,real_miles,race_miles,source_payload FROM unified_provider_job_events WHERE provider_job_id IN ('8827202','8827203') ORDER BY provider_job_id")
            self.assertGreater(rows[0]['real_miles'],0);self.assertEqual(rows[0]['race_miles'],0)
            self.assertGreater(rows[1]['race_miles'],0);self.assertEqual(rows[1]['real_miles'],0)
            self.assertEqual(json.loads(rows[1]['source_payload'])['classification_evidence']['max_speed_kmh'],'132')
            before=[dict(row) for row in rows]
        with patch.object(api,'fetch_trucky_jobs_page',AsyncMock(return_value=[{**records[1],'max_speed_kmh':129}])):
            self.assertEqual((await api.ingest_authenticated_completed_jobs(self.pool))['counts']['conflicting_event_pending'],1)
        async with self.pool.acquire() as conn:
            self.assertEqual(before,[dict(row) for row in await conn.fetch("SELECT provider_job_id,real_miles,race_miles,source_payload FROM unified_provider_job_events WHERE provider_job_id IN ('8827202','8827203') ORDER BY provider_job_id")])
            self.assertEqual(await conn.fetchval('SELECT COUNT(*) FROM unified_v2_production_credit_ledger WHERE discord_user_id=$1',user),0)
            self.assertIsNone(await conn.fetchval('SELECT real_miles FROM driver_progress WHERE discord_user_id=$1',user))

    async def test_signed_identity_receipt_advances_only_after_exact_api_job(self):
        from unittest.mock import AsyncMock
        from unified_driver_profiles import record_trucky_webhook_receipt,attest_trucky_receipt_identity
        import trucky_jobs_api_v2 as api
        await self.install_ledger()
        user, account, job_id = 991234567890127101,'8827101','8827102'
        await self.seed_verified_driver_job(user,'trucky','8827100',account,'1')
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE unified_driver_profiles SET status='active' WHERE discord_user_id=$1",user)
        receipt=await record_trucky_webhook_receipt(self.pool,'7'*64,'job_completed',job_id)
        raw_id=receipt['receipt_id'] if isinstance(receipt,dict) else receipt
        self.assertTrue(await attest_trucky_receipt_identity(self.pool,raw_id,job_id,'7'*64,user,account))
        record=dict(id=int(job_id),user_id=int(account),company_id=49041,status='completed',
                    game_id=1,completed_at='2026-10-19T12:00:00Z',stats_type='real_miles',distance_unit='km',driven_distance_km=176,real_driven_distance_km=200)
        with patch.object(api,'fetch_trucky_jobs_page',AsyncMock(return_value={'data':[record]})):
            result=await api.ingest_authenticated_completed_jobs(self.pool)
            replay=await api.ingest_authenticated_completed_jobs(self.pool)
        self.assertEqual(result['counts']['recorded'],1)
        self.assertEqual(result['counts']['receipts_verified'],1)
        self.assertEqual(replay['counts']['replayed'],1)
        self.assertEqual(replay['counts']['receipts_verified'],0)
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval('SELECT review_status FROM unified_trucky_webhook_receipts WHERE receipt_id=$1',raw_id),'verified')
            self.assertEqual(await conn.fetchval('SELECT COUNT(*) FROM unified_v2_production_credit_ledger WHERE discord_user_id=$1',user),0)
        # No retrospective identity inference for an old receipt, even though
        # the API finds a job and its account has a verified link.
        old_job={**record,'id':8827103}
        await record_trucky_webhook_receipt(self.pool,'8'*64,'job_completed','8827103')
        with patch.object(api,'fetch_trucky_jobs_page',AsyncMock(return_value={'data':[old_job]})):
            pending=await api.ingest_authenticated_completed_jobs(self.pool)
        self.assertEqual(pending['counts']['signed_receipt_pending'],1)

    async def test_approved_batch_credits_once_and_does_not_let_pending_jobs_block_others(self):
        import json
        from datetime import timedelta
        from unified_production_ledger_adapter_v2 import process_approved_mileage_batch
        await self.install_ledger()
        user=991234568887001
        account='998887001'
        for job_id in ('998887001','998887002'):
            await self.seed_verified_driver_job(user,'trucky',job_id,account,'0.125')
        async with self.pool.acquire() as c:
            await c.execute("UPDATE unified_driver_profiles SET status='active' WHERE discord_user_id=$1",user)
            await c.execute("""UPDATE unified_provider_job_events SET source_payload=
                jsonb_set(source_payload,'{job_metadata}',$1::jsonb)
                WHERE provider='trucky' AND provider_job_id IN ('998887001','998887002')""",
                json.dumps(dict(game='ETS2',origin='Paris',destination='Lille',cargo='Pet Food')))
            await c.execute("""INSERT INTO unified_trucky_webhook_receipts(payload_sha256,event_type,candidate_job_id,
                review_status,identity_discord_user_id,identity_provider_account_id,identity_verified_at)
                VALUES($1,'job_completed','998887002','verified',$2,$3,NOW())""",'9'*64,user,account)
        feed={**self.complete_feed(),'watermark':datetime(2026,10,20,tzinfo=timezone.utc).isoformat()}
        runtime={**self.runtime,'mileage_jobs':[dict(provider='trucky',provider_job_id=job_id,
            feed_evidence=feed,reconciliation_status='no_candidate') for job_id in ('998887001','998887002')]}
        first=await process_approved_mileage_batch(self.pool,config=self.config,runtime=runtime)
        self.assertEqual(first['counts']['ownership_or_receipt_pending'],1)
        self.assertEqual(first['counts']['credited'],1)
        second=await process_approved_mileage_batch(self.pool,config=self.config,runtime=runtime)
        self.assertEqual(second['counts']['duplicate_provider_job'],1)
        self.assertFalse(second['mileage_awarded'])
        async with self.pool.acquire() as c:
            self.assertEqual(await c.fetchval('SELECT combined_real_miles FROM unified_v2_production_mileage_totals WHERE discord_user_id=$1',user),Decimal('0.125'))
            self.assertEqual(await c.fetchval('SELECT COUNT(*) FROM driver_progress WHERE discord_user_id=$1',user),0)



if __name__ == "__main__":
    unittest.main()


