"""Read-only DB adapter regression tests with isolated fake connections."""
import asyncio
from decimal import Decimal
import pytest
from unified_real_mileage_db_audit_v2 import audit_unified_real_mileage


class Connection:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, query, user_id):
        assert "verified_at IS NOT NULL" in query
        assert "unified_job_matches" in query
        assert user_id == 123
        return self.rows


class Context:
    def __init__(self, rows):
        self.rows = rows

    async def __aenter__(self):
        return Connection(self.rows)

    async def __aexit__(self, *args):
        return False


class Pool:
    def __init__(self, rows):
        self.rows = rows

    def acquire(self):
        return Context(self.rows)


def job(provider, key, real, race=0, status="confirmed"):
    return dict(provider=provider, canonical_job_key=key,
                real_miles=real, race_miles=race, review_status=status)


def test_verified_real_deduplicates_and_excludes_race():
    result = asyncio.run(audit_unified_real_mileage(Pool([
        job("trucksbook", "a", Decimal("120")),
        job("trucky", "a", Decimal("118")),
        job("trucky", "b", 0, 60000),
        job("trucky", "c", 42),
    ]), 123))
    assert result["confirmed_unique_real_miles"] == Decimal("160")
    assert result["cross_provider_repeat_count"] == 1
    assert result["auto_award_allowed"] is False


def test_mixed_real_and_race_record_not_credited():
    result = asyncio.run(audit_unified_real_mileage(Pool([
        job("trucky", "mixed", 100, 50),
        job("trucky", "valid", 30),
    ]), 123))
    assert result["confirmed_unique_real_miles"] == Decimal("30")


@pytest.mark.parametrize("user_id", [0, -1, "123", True])
def test_invalid_user_rejected(user_id):
    with pytest.raises(ValueError):
        asyncio.run(audit_unified_real_mileage(Pool([]), user_id))
