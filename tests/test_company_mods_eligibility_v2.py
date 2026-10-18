"""Provider-specific Company Mods policy regression tests; no DB or Discord."""
import pytest
from decimal import Decimal
from company_mods_eligibility_v2 import evaluate_company_mods_eligibility as check


@pytest.mark.parametrize("tb,tr,eligible,provider", [
    (0, 0, False, None),
    (39_999, 59_999, False, None),
    (40_000, 0, True, "trucksbook"),
    (0, 60_000, True, "trucky"),
    (40_000, 60_000, True, "both"),
    (39_999, 60_000, True, "trucky"),
    (40_000, 59_999, True, "trucksbook"),
    (0, 59_999, False, None),
])
def test_either_provider_qualifies(tb, tr, eligible, provider):
    result = check(trucksbook_real_miles=tb, trucky_race_miles=tr)
    assert result.eligible is eligible
    assert result.qualifying_provider == provider


@pytest.mark.parametrize("tb,tr", [(-1, 0), (0, -1), (1.5, 0), (True, 0), (float("inf"), 0)])
def test_invalid_totals_rejected(tb, tr):
    with pytest.raises(ValueError):
        check(trucksbook_real_miles=tb, trucky_race_miles=tr)


def test_only_trucksbook_real_counts():
    # TrucksBook Race Miles are intentionally not accepted as input.
    assert check(trucksbook_real_miles=0, trucky_race_miles=0).eligible is False


def test_only_trucky_race_counts():
    # Trucky Real Miles must not be substituted for Trucky Race Miles.
    assert check(trucksbook_real_miles=39_999, trucky_race_miles=59_999).eligible is False


def test_read_only_audit_defaults_to_no_unlock():
    import asyncio
    from company_mods_mileage_audit_v2 import audit_company_mods_mileage

    class Connection:
        async def fetch(self, query, user_id):
            assert "verified_at IS NOT NULL" in query
            return [
                dict(provider="trucksbook", real_miles=40000, race_miles=0, review_status="confirmed"),
                dict(provider="trucky", real_miles=0, race_miles=60000, review_status="confirmed"),
                dict(provider="trucky", real_miles=0, race_miles=100000, review_status="rejected"),
                dict(provider="trucksbook", real_miles=100000, race_miles=0, review_status=None),
            ]

    class Context:
        async def __aenter__(self):
            return Connection()
        async def __aexit__(self, *args):
            return False

    class Pool:
        def acquire(self):
            return Context()

    result = asyncio.run(audit_company_mods_mileage(Pool(), 123))
    assert result["trucksbook_confirmed_real_miles"] == 40000
    assert result["trucky_confirmed_race_miles"] == 60000
    assert result["rejected_job_count"] == 1
    assert result["unreviewed_job_count"] == 1
    assert result["auto_unlock_allowed"] is False
    assert result["history_complete"] is False

@pytest.mark.parametrize("tb,tr,eligible,provider", [
    (Decimal("39999.999"), Decimal("59999.999"), False, None),
    (Decimal("40000.000"), Decimal("0.125"), True, "trucksbook"),
    (Decimal("0.125"), Decimal("60000.000"), True, "trucky"),
    (Decimal("40000.001"), Decimal("60000.001"), True, "both"),
])
def test_fractional_thresholds_are_exact(tb, tr, eligible, provider):
    from decimal import Decimal
    result = check(trucksbook_real_miles=tb, trucky_race_miles=tr)
    assert result.eligible is eligible
    assert result.qualifying_provider == provider
    assert isinstance(result.trucksbook_real_miles, Decimal)
    assert isinstance(result.trucky_race_miles, Decimal)

def test_only_trucksbook_real_counts():