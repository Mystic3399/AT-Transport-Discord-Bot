"""Regression tests for read-only unified Real Miles reconciliation."""
from decimal import Decimal
from unified_real_mileage_audit_v2 import summarize_confirmed_real_miles


def row(provider, key, miles, status="confirmed", category="real"):
    return {"provider": provider, "canonical_job_key": key,
            "real_miles": miles, "review_status": status,
            "mileage_type": category}


def test_cross_provider_same_journey_is_counted_once():
    result = summarize_confirmed_real_miles([
        row("trucksbook", "job-a", "120"),
        row("trucky", "job-a", "118"),
        row("trucky", "job-b", "80"),
    ])
    assert result["confirmed_unique_real_miles"] == Decimal("198")
    assert result["unique_job_count"] == 2
    assert result["cross_provider_repeat_count"] == 1
    assert result["auto_award_allowed"] is False


def test_unreviewed_rejected_unkeyed_and_race_not_counted():
    result = summarize_confirmed_real_miles([
        row("trucksbook", "a", 100, "unreviewed"),
        row("trucky", "b", 200, "rejected"),
        row("trucky", None, 300),
        row("trucky", "race", 400, category="race"),
        row("trucky", "c", 50),
    ])
    assert result["confirmed_unique_real_miles"] == Decimal("50")
    assert result["unreviewed_job_count"] == 1
    assert result["rejected_job_count"] == 1
    assert result["invalid_job_count"] == 1


def test_bad_distance_cannot_inflate_totals():
    result = summarize_confirmed_real_miles([
        row("trucky", "a", "NaN"),
        row("trucksbook", "b", -20),
        row("trucky", "c", "Infinity"),
    ])
    assert result["confirmed_unique_real_miles"] == 0
    assert result["invalid_job_count"] == 3
    assert result["history_complete"] is False


def test_same_provider_canonical_collision_excluded():
    result = summarize_confirmed_real_miles([
        row("trucky", "collision", 200),
        row("trucky", "collision", 200),
        row("trucksbook", "clean", 30),
    ])
    assert result["confirmed_unique_real_miles"] == Decimal("30")
    assert result["unique_job_count"] == 1
    assert result["invalid_job_count"] == 1
    assert result["ambiguous_canonical_key_count"] == 1
    assert result["auto_award_allowed"] is False
