"""Transactional regression checks using the existing disposable DB fixture."""
import ast
import asyncio
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from test_trucky_linked_allocation_v2 import LinkedAllocationDatabaseTests, observation, asyncpg
from trucky_linked_allocation_v2 import vault_candidate, _settle_candidate
from automatic_linked_mileage_v2 import SCHEMA as AUTOMATIC_SCHEMA
from pending_driver_history_v2 import SCHEMA as HISTORY_SCHEMA


@unittest.skipUnless(asyncpg is not None and os.getenv("AT_V2_TEST_DATABASE_DISPOSABLE") == "YES"
                     and "/v2_test" in os.getenv("TEST_DATABASE_URL", ""), "Disposable PostgreSQL only")
class AutomaticDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await LinkedAllocationDatabaseTests.asyncSetUp(self)
        async with self.pool.acquire() as c:
            await c.execute(HISTORY_SCHEMA)
            await c.execute(AUTOMATIC_SCHEMA)
            await c.execute("""
                ALTER TABLE processed_jobs ADD COLUMN accepted_distance integer DEFAULT 0,
                    ADD COLUMN statistics text DEFAULT 'Real', ADD COLUMN trucksbook_name text DEFAULT '';
                """)
        node = next(n for n in ast.parse(Path("bot.py").read_text()).body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == "record_real_job")
        ns = dict(os=os, db_pool=self.pool, PROGRESSION_ROLES=[])
        exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), ns)
        self.record = ns["record_real_job"]
        self.env = patch.dict(os.environ, {"AT_V2_AUTOMATIC_LINKED_RECONCILIATION": "YES",
                                          "AT_V2_ISOLATED_STAGING": "",
                                          "AT_V2_PRODUCTION_MILEAGE_ENABLED": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    async def asyncTearDown(self):
        await LinkedAllocationDatabaseTests.asyncTearDown(self)

    async def prepare(self, km, tb_miles):
        self.observation = observation(km=km, game="ETS2", origin="London",
                                       destination="Dover", cargo="Apples")
        evidence = dict(game="ETS2", origin="London", destination="Dover", cargo="Apples",
                        unit="mi", distance=tb_miles, stats_type="Real",
                        source_message_at="2026-10-01T12:01:00+00:00")
        async with self.pool.acquire() as c:
            for provider, job_id, account, source, payload in (
                ("trucky", "701", "456", "authenticated_api", self.observation["evidence"]),
                ("trucksbook", "801", None, "verified_webhook_source", evidence),
            ):
                await c.execute("""
                    INSERT INTO pending_provider_job_history_v2
                        (provider,provider_job_id,provider_account_id,source_kind,evidence,payload_sha256)
                    VALUES($1,$2,$3,$4,$5::jsonb,$6)
                    """, provider, job_id, account, source, json.dumps(payload), "a"*64)
        self.tb_miles = tb_miles

    async def trucky(self):
        async with self.pool.acquire() as c:
            return await _settle_candidate(c, vault_candidate([self.observation], 789), {"456"})

    async def tb(self):
        return await self.record("801", "Test Driver", 789, self.tb_miles)

    async def assert_total(self, amount, winner):
        async with self.pool.acquire() as c:
            self.assertEqual(await c.fetchval("SELECT real_miles FROM driver_progress"), amount)
            self.assertEqual(await c.fetchval("SELECT winner FROM automatic_linked_journey_pairs_v2"), winner)
            self.assertEqual(await c.fetchval("SELECT COUNT(*) FROM automatic_linked_journey_pairs_v2"), 1)
            count = await c.fetchval("SELECT COUNT(*) FROM unified_v2_production_credit_ledger")
            self.assertEqual(count, 1 if winner == "trucky" else 0)

    async def test_trucky_higher_tb_first_and_retries(self):
        await self.prepare("418.42944", 200)
        await self.tb()
        self.assertEqual(await self.trucky(), "credited_real")
        await self.assert_total(260, "trucky")
        await asyncio.gather(self.trucky(), self.tb(), self.trucky(), self.tb())
        await self.assert_total(260, "trucky")

    async def test_trucky_higher_trucky_first(self):
        await self.prepare("418.42944", 200)
        await self.trucky()
        await self.tb()
        await self.assert_total(260, "trucky")

    async def test_trucksbook_higher_trucky_first(self):
        await self.prepare("160.9344", 200)
        await self.trucky()
        await self.tb()
        await self.assert_total(200, "trucksbook")
        await self.trucky()
        await self.tb()
        await self.assert_total(200, "trucksbook")

    async def test_trucksbook_higher_tb_first(self):
        await self.prepare("160.9344", 200)
        await self.tb()
        self.assertEqual(await self.trucky(), "duplicate_provider_job")
        await self.assert_total(200, "trucksbook")

    async def test_simultaneous_arrivals_credit_one_winner(self):
        await self.prepare("418.42944", 200)
        await asyncio.gather(self.trucky(), self.tb())
        await self.assert_total(260, "trucky")

    async def test_replacement_failure_rolls_back_pair_and_mileage(self):
        await self.prepare("418.42944", 200)
        await self.tb()
        async with self.pool.acquire() as c:
            await c.execute("""
                CREATE FUNCTION reject_pair_credit() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'replacement failure'; END; $$;
                CREATE TRIGGER reject_pair_credit BEFORE INSERT ON unified_v2_production_credit_ledger
                FOR EACH ROW EXECUTE FUNCTION reject_pair_credit();
                """)
        with self.assertRaises(Exception):
            await self.trucky()
        async with self.pool.acquire() as c:
            self.assertEqual(await c.fetchval("SELECT real_miles FROM driver_progress"), 200)
            self.assertEqual(await c.fetchval("SELECT COUNT(*) FROM automatic_linked_journey_pairs_v2"), 0)
            self.assertEqual(await c.fetchval(
                "SELECT COUNT(*) FROM unified_v2_production_mileage_decisions WHERE decision='duplicate_provider_job'"), 0)
