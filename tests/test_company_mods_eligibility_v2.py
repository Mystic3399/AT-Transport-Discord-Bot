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


def test_legacy_trucksbook_real_input_remains_supported():
    # TrucksBook Race Miles are intentionally not accepted as input.
    assert check(trucksbook_real_miles=0, trucky_race_miles=0).eligible is False


def test_real_and_race_thresholds_remain_separate():
    # Sub-threshold Real Miles and Race Miles must not be added together.
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
    result = check(trucksbook_real_miles=tb, trucky_race_miles=tr)
    assert result.eligible is eligible
    assert result.qualifying_provider == provider
    assert isinstance(result.trucksbook_real_miles, Decimal)
    assert isinstance(result.trucky_race_miles, Decimal)



@pytest.mark.parametrize('combined,race,eligible',[
    (Decimal('39999.9999999999'),0,False),
    (Decimal('40000'),0,True),
    (Decimal('40000.0000000001'),0,True),
    (0,Decimal('59999.9999999999'),False),
    (0,Decimal('60000'),True),
])
def test_canonical_combined_real_or_trucky_race(combined,race,eligible):
    result=check(combined_real_miles=combined,trucky_race_miles=race)
    assert result.eligible is eligible
    assert result.combined_real_miles == combined


def test_combined_real_total_already_includes_both_providers():
    result=check(trucksbook_real_miles=25000,combined_real_miles=40000,trucky_race_miles=0)
    assert result.eligible
    assert result.combined_real_miles == 40000  # never add the legacy subtotal twice
    assert result.qualifying_provider == 'combined_real'
    with pytest.raises(ValueError):
        check(trucksbook_real_miles=40000,combined_real_miles=39999)


def test_real_mileage_bucket_accepts_both_providers_but_excludes_tb_race():
    from unified_mileage_engine_control_v2 import policy_mileage_buckets
    for provider in ('trucksbook','trucky'):
        buckets=policy_mileage_buckets(provider,'real',Decimal('176'))
        assert buckets['company_mods_combined_real_miles'] == Decimal('176')
    assert policy_mileage_buckets('trucky','race',Decimal('176'))['company_mods_combined_real_miles'] == 0
    with pytest.raises(ValueError):
        policy_mileage_buckets('trucksbook','race',Decimal('176'))
