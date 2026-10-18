"""Fail-closed Trucky Race evidence regression tests."""
import pytest
from decimal import Decimal
from trucky_race_evidence_review_v2 import review_trucky_race_evidence as review


def evidence(**overrides):
    args = dict(account_verified=True, company_verified=True,
                job_completed=True, statistics="Race",
                distance_km="160.934", provider_job_id="27805272")
    args.update(overrides)
    return review(**args)


def test_verified_race_remains_review_only():
    result = evidence()
    assert result["status"] == "needs_provider_api_verification"
    assert result["kilometers"] == Decimal("160.934")
    assert result["awardable"] is False
    assert result["company_mods_unlock_allowed"] is False


def test_wotr_race_does_not_become_real():
    result = evidence(statistics="Race, WoTr")
    assert result["world_of_trucks"] is True
    assert result["awardable"] is False


@pytest.mark.parametrize("change", [
    {"account_verified": False},
    {"company_verified": False},
    {"job_completed": False},
    {"statistics": "Real, WoTr"},
    {"statistics": "Race, Real"},
    {"statistics": "WoTr"},
    {"distance_km": "NaN"},
    {"distance_km": -1},
    {"distance_km": 0},
    {"provider_job_id": "not-a-number"},
])
def test_unverified_or_malformed_race_evidence_rejected(change):
    result = evidence(**change)
    assert result["status"] == "rejected"
    assert result["awardable"] is False
