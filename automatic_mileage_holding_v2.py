"""Durable, source-keyed holding area for automatic A&T mileage recovery.

Jobs do not become payable by being in the queue. The existing verified ledger
and source/reconciliation checks alone can credit a job. Never infer a Discord
identity from a display name or silently reclassify an immutable credit.
"""
import re
from decimal import Decimal, InvalidOperation

STATES = frozenset({
    'awaiting_company_membership',
    'awaiting_account_link',
    'awaiting_source_evidence',
    'awaiting_duplicate_evidence',
    'awaiting_historical_baseline',
    'provider_conflict',
    'excluded',
    'credited',
})
VALID_KINDS = frozenset({'real', 'race', 'unknown'})
SCHEMA = """
CREATE TABLE IF NOT EXISTS automatic_mileage_holding_v2 (
 provider TEXT NOT NULL CHECK(provider IN ('trucksbook','trucky')),
 provider_job_id TEXT NOT NULL CHECK(provider_job_id ~ '^[1-9][0-9]*$'),
 provider_account_id TEXT,
 discord_user_id BIGINT,
 mileage_type TEXT NOT NULL CHECK(mileage_type IN ('real','race','unknown')),
 source_miles NUMERIC(16,3),
 state TEXT NOT NULL CHECK(state IN (
   'awaiting_company_membership','awaiting_account_link',
   'awaiting_source_evidence','awaiting_duplicate_evidence',
   'awaiting_historical_baseline','provider_conflict','excluded','credited')),
 reason_code TEXT NOT NULL,
 first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 credited_at TIMESTAMPTZ,
 PRIMARY KEY(provider,provider_job_id),
 CHECK(source_miles IS NULL OR source_miles>0),
 CHECK(discord_user_id IS NULL OR discord_user_id>0)
);
CREATE INDEX IF NOT EXISTS automatic_mileage_holding_state_idx
 ON automatic_mileage_holding_v2(state,provider,checked_at);
"""

def company_eligible(*, trucksbook=None, trucky=None, discord_present=True):
    """Either company membership qualifies; a definite absence in one does not veto the other."""
    if discord_present is False:
        return False
    return trucksbook == 'member' or trucky == 'member'

def membership_state(*, trucksbook=None, trucky=None, discord_present=True):
    if company_eligible(trucksbook=trucksbook,trucky=trucky,discord_present=discord_present):
        return 'eligible'
    return 'awaiting_company_membership'

def holding_state(reason):
    if reason in ('account_link_pending','provider_link_not_verified','inactive_driver',
                  'source_account_disagrees','unverified_trucksbook_account',
                  'claim_missing','unverified_owner'):
        return 'awaiting_account_link'
    if reason in ('company_not_member','member_not_in_roster',
                  'company_membership_unconfirmed','discord_not_in_server'):
        return 'awaiting_company_membership'
    if reason in ('historical_balance_pending','historical_balance_cutoff_or_time_missing',
                  'race_historical_balance_pending','duplicate_already_in_historical_baseline'):
        return 'awaiting_historical_baseline'
    if reason in ('invalid_or_conflicting_pending','conflicting_job_pending',
                  'race_conflicting_route_evidence','conflicting_route_evidence',
                  'multiple_provider_owners','source_conflict','unverified_or_conflicting_source'):
        return 'provider_conflict'
    if reason in ('invalid_source','unsupported_or_invalid_source','excluded','invalid_speed','cancelled','unlisted'):
        return 'excluded'
    if reason in ('credited','credited_real','credited_race','replayed'):
        return 'credited'
    if reason in ('historical_route_coverage_pending','route_evidence_pending',
                  'race_historical_route_coverage_pending','race_route_evidence_pending',
                  'ambiguous_journey','race_potential_duplicate','duplicate_provider_job',
                  'historical_or_trucksbook_overlap','race_historical_balance_pending'):
        return 'awaiting_duplicate_evidence'
    return 'awaiting_source_evidence'

def safe_source_miles(value):
    try:
        amount=Decimal(str(value))
    except (ValueError,InvalidOperation,TypeError):
        return None
    if not amount.is_finite() or amount<=0 or amount>1000000:
        return None
    return amount

async def store_state(conn, provider, job_id, account_id, *,
                      driver_id=None, mileage_type='unknown', miles=None,
                      reason='awaiting_source_evidence', state=None):
    """Idempotently publish a job's pending reason. This never writes totals.

    A confirmed ledger credit is final and cannot be put back into pending by
    an older source observation or an overlapping worker.
    """
    if provider not in ('trucky','trucksbook'):
        raise ValueError('Invalid provider')
    if not isinstance(job_id,str) or not re.fullmatch(r'[1-9][0-9]*',job_id):
        raise ValueError('Invalid provider job ID')
    if account_id is not None and (not isinstance(account_id,str)
            or not re.fullmatch(r'[1-9][0-9]*',account_id)):
        raise ValueError('Invalid provider account')
    if mileage_type not in VALID_KINDS:
        raise ValueError('Invalid mileage classification')
    if driver_id is not None and (type(driver_id) is not int or driver_id<=0):
        raise ValueError('Invalid driver')
    selected = state or holding_state(reason)
    if selected not in STATES:
        raise ValueError('Invalid holding state')
    source_miles=safe_source_miles(miles)
    if selected=='credited':
        # Caller must have observed authoritative processed job or ledger credit;
        # this is a status mirror, not an alternative crediting method.
        if driver_id is None:
            raise ValueError('Credited job requires a verified owner')
    await conn.execute("""
        INSERT INTO automatic_mileage_holding_v2
          (provider,provider_job_id,provider_account_id,discord_user_id,
           mileage_type,source_miles,state,reason_code,credited_at)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,
               CASE WHEN $7='credited' THEN NOW() ELSE NULL END)
        ON CONFLICT(provider,provider_job_id) DO UPDATE SET
          provider_account_id=EXCLUDED.provider_account_id,
          discord_user_id=EXCLUDED.discord_user_id,
          mileage_type=EXCLUDED.mileage_type,
          source_miles=EXCLUDED.source_miles,
          state=EXCLUDED.state,
          reason_code=EXCLUDED.reason_code,
          credited_at=CASE WHEN EXCLUDED.state='credited' THEN
              COALESCE(automatic_mileage_holding_v2.credited_at,NOW())
              ELSE automatic_mileage_holding_v2.credited_at END,
          checked_at=NOW()
        WHERE automatic_mileage_holding_v2.state<>'credited'
          AND (
            automatic_mileage_holding_v2.state IS DISTINCT FROM EXCLUDED.state
            OR automatic_mileage_holding_v2.reason_code IS DISTINCT FROM EXCLUDED.reason_code
            OR automatic_mileage_holding_v2.discord_user_id IS DISTINCT FROM EXCLUDED.discord_user_id
            OR automatic_mileage_holding_v2.provider_account_id IS DISTINCT FROM EXCLUDED.provider_account_id
            OR automatic_mileage_holding_v2.mileage_type IS DISTINCT FROM EXCLUDED.mileage_type
            OR automatic_mileage_holding_v2.source_miles IS DISTINCT FROM EXCLUDED.source_miles
          )
    """,provider,job_id,account_id,driver_id,mileage_type,source_miles,selected,reason)

async def holding_summary(conn):
    rows=await conn.fetch("""
      SELECT provider,state,COUNT(*)::BIGINT AS jobs
      FROM automatic_mileage_holding_v2
      GROUP BY provider,state ORDER BY provider,state
    """)
    return [dict(row) for row in rows]
