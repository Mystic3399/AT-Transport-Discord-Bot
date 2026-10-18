"""World of Trucks classifications must not corrupt Real/Race accounting."""
import pytest
from world_of_trucks_policy_v2 import classify_event_mileage as classify


@pytest.mark.parametrize("provider,stats,category,wotr", [
    ("trucksbook", "Real", "real", False),
    ("trucksbook", "Real, WoTr", "real", True),
    ("trucky", "Real", "real", False),
    ("trucky", "WoTr, Real", "real", True),
    ("trucky", "Race", "race", False),
    ("trucky", "Race, WoTr", "race", True),
])
def test_valid_verified_classification(provider, stats, category, wotr):
    result = classify(provider, stats)
    assert result is not None
    assert result.mileage_type == category
    assert result.world_of_trucks is wotr


@pytest.mark.parametrize("provider,stats", [
    ("trucksbook", "Race"),
    ("trucksbook", "Race, WoTr"),
    ("trucky", "WoTr"),
    ("trucky", "Real, Race, WoTr"),
    ("trucky", "Real, Real"),
    ("trucky", "Real, WoTr, WoTr"),
    ("trucky", "Event"),
    ("trucky", ""),
    ("unknown", "Real, WoTr"),
])
def test_reject_unsupported_or_ambiguous(provider, stats):
    assert classify(provider, stats) is None
