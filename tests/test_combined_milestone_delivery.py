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
