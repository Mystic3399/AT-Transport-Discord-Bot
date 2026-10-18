"""Provider-specific Company Mods policy regression tests; no DB or Discord."""
import pytest
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


@pytest.mark.parametrize("tb,tr", [(-1, 0), (0, -1), (1.5, 0), (True, 0)])
def test_invalid_totals_rejected(tb, tr):
    with pytest.raises(ValueError):
        check(trucksbook_real_miles=tb, trucky_race_miles=tr)


def test_only_trucksbook_real_counts():
    # TrucksBook Race Miles are intentionally not accepted as input.
    assert check(trucksbook_real_miles=0, trucky_race_miles=0).eligible is False


def test_only_trucky_race_counts():
    # Trucky Real Miles must not be substituted for Trucky Race Miles.
    assert check(trucksbook_real_miles=39_999, trucky_race_miles=59_999).eligible is False
