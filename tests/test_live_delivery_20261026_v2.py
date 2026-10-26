"""Actual redacted evidence remains pending; similar completion time is not proof."""
import json
from pathlib import Path
from decimal import Decimal
from datetime import datetime,timezone
from unified_reconciliation_policy_v2 import classify_delivery

EVIDENCE=json.loads(Path('tests/fixtures/live_delivery_20261026_redacted.json').read_text())

def test_exact_provider_distance_discrepancy_is_preserved():
    tb=Decimal(EVIDENCE['trucksbook']['accepted_miles'])
    tr=EVIDENCE['trucky']
    converted=(Decimal(tr['original_distance'])*Decimal('0.621371192237334')).quantize(Decimal('0.0000000001'))
    assert converted == Decimal(tr['canonical_miles'])
    assert converted != tb
    assert EVIDENCE['same_discord_driver_verified']
    assert tr['receipt_status']=='verified'
    assert not tr['mileage_awarded']

def test_missing_shared_evidence_does_not_confirm_or_credit_duplicate():
    assert EVIDENCE['shared_journey_id'] is None
    incoming=dict(provider='trucky',provider_job_id=EVIDENCE['trucky']['provider_job_id'],
        discord_user_id=123,mileage_type='real',delivered_at=datetime(2026,10,26,19,50,23,tzinfo=timezone.utc))
    assert classify_delivery(incoming,[],provider_feed_complete=False) == 'pending'
    assert EVIDENCE['reconciliation_status']=='pending'


def test_provider_driven_distance_matches_tracker_without_overwriting_old_evidence():
    from trucky_verified_job_candidate_v2 import normalize_verified_trucky_job
    tr=EVIDENCE['trucky']
    job=dict(company_id=49041,id=int(tr['provider_job_id']),user_id=1234,
        status='completed',completed_at='2026-10-26T19:50:23Z',stats_type='real_miles',distance_unit='km',
        driven_distance_km=int(tr['driven_distance_km']),real_driven_distance_km=int(tr['original_distance']))
    candidate=normalize_verified_trucky_job(job,verified_provider_account_id='1234',discord_user_id=123)
    assert candidate['kilometers'] == Decimal('586')
    miles=(candidate['kilometers']*Decimal('0.621371192237334')).quantize(Decimal('0.0000000001'))
    assert miles == Decimal(tr['corrected_canonical_miles'])
    assert int(miles) == int(tr['tracker_display_miles_owner_report'])
    assert not candidate['eligible_for_award']
    # The previous API field value remains auditable, never overwritten as though
    # it had originally been recorded correctly or granted another credit.
    assert tr['original_distance']=='635'
    job.pop('driven_distance_km')
    assert normalize_verified_trucky_job(job,verified_provider_account_id='1234',discord_user_id=123) is None
