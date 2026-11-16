import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_wotr_hotfix import load_function, NS
from mileage_audit_confirmation_v2 import reconciled_repair_delta


@pytest.mark.asyncio
async def test_missing_150k_is_delivered_from_combined_real_total():
    pending = dict(discord_user_id=42, milestone_miles=150000,
                   trucksbook_name="Mystic", real_miles=150300)
    connection = SimpleNamespace(execute=AsyncMock(), fetch=AsyncMock(return_value=[pending]))
    class Pool:
        def acquire(self):
            return self
        async def __aenter__(self):
            return connection
        async def __aexit__(self, *args):
            pass

    send = AsyncMock(return_value=True)
    deliver = load_function("deliver_pending_real_milestones", dict(
        NS, db_pool=Pool(), os=os, MILESTONE_ANNOUNCEMENT_CHANNEL_ID=99,
        active_driver_ids=lambda guild: [42], announce_progression_milestone=send,
        PROGRESSION_ROLES=[(0, 1, "New Driver"), (150000, 2, "Viking Pathfinder")],
    ))
    member = object()
    guild = SimpleNamespace(get_member=lambda user_id: member)
    await deliver(guild)
    assert connection.execute.call_args.args[1:] == ([150000], [42])
    send.assert_awaited_once_with(guild, member, 42, "Mystic",
                                  {"miles": 150000, "rank": "Viking Pathfinder"}, 150300)


@pytest.mark.asyncio
async def test_repair_uses_higher_mileage_delta_for_dual_provider_driver(monkeypatch):
    from decimal import Decimal
    import automatic_linked_mileage_v2

    monkeypatch.setenv("AT_V2_AUTOMATIC_LINKED_RECONCILIATION", "YES")
    compare = AsyncMock(return_value=(Decimal(21), None))
    monkeypatch.setattr(automatic_linked_mileage_v2, "reconcile_trucksbook", compare)
    conn = SimpleNamespace(fetchval=AsyncMock(return_value=True))
    candidate = dict(job_id="12", discord_user_id=42, missing_distance=1021,
                     recorded_distance=0, actual_distance=1021, repair_kind="missing_real")
    assert await reconciled_repair_delta(conn, candidate) == (21, None)
    compare.assert_awaited_once_with(conn, "12", 42, 1021)
    compare.return_value = (None, "route_evidence_pending")
    assert await reconciled_repair_delta(conn, candidate) == (None, "route_evidence_pending")


@pytest.mark.asyncio
async def test_postgres_preview_rolls_back_provider_replacement_and_confirm_applies_delta(monkeypatch):
    import json
    from datetime import datetime, timezone
    from decimal import Decimal
    from urllib.parse import urlsplit
    from uuid import uuid4

    import asyncpg
    import automatic_linked_mileage_v2 as matching
    from mileage_audit_confirmation_v2 import SCHEMA, create_mileage_audit_preview
    from pending_driver_history_v2 import SCHEMA as HISTORY
    from unified_driver_profiles import SCHEMA as PROFILES
    from unified_production_ledger_adapter_v2 import SCHEMA as LEDGER

    url = os.getenv("TEST_DATABASE_URL", "")
    parsed = urlsplit(url)
    if (os.getenv("AT_V2_TEST_DATABASE_DISPOSABLE") != "YES"
            or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
            or parsed.path != "/v2_test"):
        pytest.skip("Requires disposable PostgreSQL")
    monkeypatch.setenv("AT_V2_AUTOMATIC_LINKED_RECONCILIATION", "YES")
    monkeypatch.setenv("TRUCKSBOOK_HISTORY_WEBHOOK_IDS", "123")
    schema = "audit_" + uuid4().hex
    conn = await asyncpg.connect(url)
    await conn.execute(f"CREATE SCHEMA {schema}")
    await conn.execute(f"SET search_path TO {schema}")
    # Keep the entire scenario on one connection, including the preview.
    class Pool:
        def acquire(self):
            return self
        async def __aenter__(self):
            return conn
        async def __aexit__(self, *args):
            pass
    try:
        for sql in (PROFILES, LEDGER, HISTORY, matching.SCHEMA, SCHEMA):
            await conn.execute(sql)
        await conn.execute("""
            CREATE TABLE driver_links (discord_user_id BIGINT, trucksbook_user_id BIGINT);
            CREATE TABLE driver_progress (discord_user_id BIGINT, real_miles BIGINT);
            CREATE TABLE mileage_repair_candidates (
                job_id TEXT, discord_user_id BIGINT, trucksbook_name TEXT,
                recorded_distance INTEGER, actual_distance INTEGER, missing_distance INTEGER,
                source_channel_id BIGINT, source_message_id BIGINT, repair_kind TEXT,
                source_delivered_at TIMESTAMPTZ, audited_at TIMESTAMPTZ, source_webhook_id BIGINT,
                source_provider_account_id TEXT, applied_at TIMESTAMPTZ);
            INSERT INTO driver_links VALUES (42, 1234);
            INSERT INTO driver_progress VALUES (42, 1000);
            INSERT INTO mileage_repair_candidates
            VALUES ('2001',42,'Mystic',0,1021,1021,1,2,'missing_real',NOW(),NOW(),123,'1234',NULL);
            INSERT INTO unified_driver_profiles(discord_user_id,status) VALUES(42,'active');
            INSERT INTO unified_v2_production_mileage_totals
                (discord_user_id,combined_real_miles,trucky_race_miles) VALUES(42,1000,0);
        """)
        await conn.execute("""INSERT INTO unified_v2_production_credit_ledger
            (provider,provider_job_id,discord_user_id,provider_account_id,mileage_type,miles,
             canonical_credit_key,source_evidence_sha256,ownership_evidence_sha256)
            VALUES('trucky','3001',42,'4001','real',1000,'accepted-link-trucky:3001',$1,$1)
        """, 'a' * 64)
        now = datetime.now(timezone.utc)
        route = dict(game="ets2", origin="London", destination="Dover", cargo="Cars")
        await conn.execute("""INSERT INTO pending_provider_job_history_v2
            (provider,provider_job_id,payload_sha256,provider_account_id,source_kind,evidence)
            VALUES('trucksbook','2001',$1,'1234','verified_webhook_source',$2)
        """, 'b' * 64, json.dumps(dict(route, unit="mi", distance=1021,
                                         stats_type="Real", source_message_at=now.isoformat())))
        monkeypatch.setattr(matching, "linked_trucky_comparisons", AsyncMock(return_value=[
            dict(provider="trucky", provider_job_id="3001", discord_user_id=42,
                 mileage_type="real", miles=Decimal(1000), delivered_at=now, metadata=route),
        ]))
        preview = await create_mileage_audit_preview(Pool(), 1, 2, 3, ["2001"])
        assert preview["addition"] == 21
        assert await conn.fetchval("SELECT COUNT(*) FROM unified_v2_production_credit_ledger") == 1
        assert await conn.fetchval("SELECT COUNT(*) FROM automatic_linked_journey_pairs_v2") == 0
        candidate = await conn.fetchrow("SELECT * FROM mileage_repair_candidates")
        async with conn.transaction():
            assert await reconciled_repair_delta(conn, candidate) == (21, None)
        assert await conn.fetchval("SELECT COUNT(*) FROM unified_v2_production_credit_ledger") == 0
        assert await conn.fetchval("SELECT winner FROM automatic_linked_journey_pairs_v2") == "trucksbook"
    finally:
        await conn.execute(f"DROP SCHEMA {schema} CASCADE")
        await conn.close()
