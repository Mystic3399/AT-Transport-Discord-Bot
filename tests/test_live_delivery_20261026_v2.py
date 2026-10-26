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
