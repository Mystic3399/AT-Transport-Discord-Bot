from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import race_progression as race

RANKS = [(0, 1, "New Driver"), (5000, 2, "Road Runner"), (100000, 3, "Centurion")]
ACHIEVEMENTS = [(40000, 4, "40K Mileage Club"), (100000, 5, "100K Achievement")]
HALL = [(250000, 6, "Veteran"), (500000, 7, "Elite")]


def test_race_thresholds_use_only_race_and_keep_highest_rank():
    assert race.targets(0, RANKS, ACHIEVEMENTS, HALL) == set()
    assert race.targets("39999.99", RANKS, ACHIEVEMENTS, HALL) == {("rank", 5000)}
    assert race.targets(40000, RANKS, ACHIEVEMENTS, HALL) == {("rank", 5000), ("achievement", 40000)}
    assert race.targets(500000, RANKS, ACHIEVEMENTS, HALL) == {
        ("rank", 100000), ("achievement", 40000), ("achievement", 100000), ("hall", 500000)}


@pytest.mark.asyncio
async def test_mirror_roles_never_copy_real_permissions_or_recreate_saved_roles():
    roles = {1: SimpleNamespace(colour=123)}
    guild = SimpleNamespace(id=9, roles=[], get_role=roles.get)
    async def create(**kwargs):
        assert kwargs["permissions"].value == 0
        assert kwargs["name"].endswith("Race Mileage")
        role = SimpleNamespace(id=100 + len(roles), **kwargs)
        roles[role.id] = role
        return role
    guild.create_role = AsyncMock(side_effect=create)
    conn = SimpleNamespace(fetch=AsyncMock(return_value=[]), execute=AsyncMock())
    mapped = await race.ensure_roles(conn, guild, RANKS, ACHIEVEMENTS, HALL)
    assert len(mapped) == 7
    conn.fetch.return_value = [dict(kind=k[0], threshold=k[1], role_id=v) for k, v in mapped.items()]
    guild.create_role.reset_mock()
    assert await race.ensure_roles(conn, guild, RANKS, ACHIEVEMENTS, HALL) == mapped
    guild.create_role.assert_not_awaited()


@pytest.mark.asyncio
async def test_race_sync_preserves_real_roles_and_permanent_race_awards():
    roles = {i: SimpleNamespace(id=i) for i in (10, 11, 12, 13, 99)}
    member = SimpleNamespace(roles=[roles[10], roles[99]], add_roles=AsyncMock(), remove_roles=AsyncMock())
    guild = SimpleNamespace(get_member=lambda uid: member, get_role=roles.get)
    mapped = {("rank", 0): 10, ("rank", 5000): 11, ("achievement", 40000): 12, ("hall", 500000): 13}
    conn = SimpleNamespace(execute=AsyncMock(), fetch=AsyncMock(return_value=[
        dict(kind="achievement", threshold=40000), dict(kind="hall", threshold=500000)]))
    assert await race.sync_driver(conn, guild, 42, 5000, mapped, RANKS, ACHIEVEMENTS, HALL)
    assert {r.id for r in member.add_roles.call_args.args} == {11, 12, 13}
    assert {r.id for r in member.remove_roles.call_args.args} == {10}
    assert all("real" not in call.args[0].lower() for call in conn.execute.call_args_list)
