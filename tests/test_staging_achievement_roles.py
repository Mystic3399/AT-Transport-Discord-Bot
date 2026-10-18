"""Isolated achievement-role regression tests; no Discord network or database."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

SOURCE = Path(__file__).resolve().parents[1] / "bot.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))
NODE = next(n for n in TREE.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "sync_member_permanent_roles")

class Member:
    def __init__(self):
        self.roles = []
        self.add_roles = AsyncMock(side_effect=self.add)
        self.remove_roles = AsyncMock(side_effect=self.remove)

    async def add(self, *roles, **kwargs):
        self.roles.extend(roles)

    async def remove(self, *roles, **kwargs):
        self.roles = [r for r in self.roles if r not in roles]


def run_sync(miles, member):
    role = SimpleNamespace(id=1561168545994252431)
    guild = SimpleNamespace(get_member=lambda uid: member, get_role=lambda rid: role if rid == role.id else None)
    namespace = {
        "ACHIEVEMENT_ROLES": [(40000, role.id, "40K Mileage Club")],
        "HALL_OF_FAME_ROLES": [],
        "get_hall_of_fame_tier": lambda miles: None,
    }
    exec(compile(ast.Module(body=[NODE], type_ignores=[]), str(SOURCE), "exec"), namespace)
    return asyncio.run(namespace["sync_member_permanent_roles"](guild, 613036707863986236, miles))


def test_below_40k_no_award():
    member = Member()
    assert run_sync(39999, member)["status"] == "correct"
    assert member.roles == []


def test_at_40k_awards_role():
    member = Member()
    assert run_sync(40000, member)["status"] == "updated"
    assert [r.id for r in member.roles] == [1561168545994252431]


def test_repeated_sync_idempotent():
    member = Member()
    run_sync(40000, member)
    assert run_sync(40000, member)["status"] == "correct"
    assert len(member.roles) == 1
    assert member.add_roles.await_count == 1


def test_missing_member_safe():
    assert run_sync(40000, None)["status"] == "member_missing"


def test_above_40k_awards_role():
    member = Member()
    assert run_sync(40001, member)["status"] == "updated"
    assert [r.id for r in member.roles] == [1561168545994252431]


def test_achievement_not_removed_on_lower_mileage():
    member = Member()
    run_sync(40000, member)
    assert run_sync(39999, member)["status"] == "correct"
    assert [r.id for r in member.roles] == [1561168545994252431]
    member.remove_roles.assert_not_awaited()
