"""Allocate authenticated vault jobs under the owner's accepted-link policy.

Linked is not relabelled Verified. Race is independent of legacy Real balances.
Real is held whenever historical/other-provider overlap cannot be excluded.
Uses the existing production ledger; never writes registration or Discord roles.
"""
from collections import defaultdict
from decimal import Decimal, ROUND_FLOOR
import hashlib
import json
import os
import re

from pending_race_mileage_v2 import merge_history_observations
from unified_distance_units import KM_TO_MILES
from unified_production_ledger_adapter_v2 import SCHEMA
from unified_production_mileage_gate_v2 import read_only_database_fingerprint
from trucky_verified_job_candidate_v2 import normalize_verified_trucky_job

POLICY = 'steam_registered_company_member_link_v1'


def activation_allowed(config=None):
    c = os.environ if config is None else config
    return (
        c.get('AT_V2_LINKED_TRUCKY_MILEAGE_ENABLED') == 'YES'
        and c.get('AT_V2_PENDING_HISTORY_ENABLED') == 'YES'
        and c.get('AT_V2_TRUCKY_API_INGEST_ENABLED') == 'YES'
        and all(c.get(k) != 'YES' for k in (
            'AT_V2_ISOLATED_STAGING', 'AT_V2_TEST_DATABASE_DISPOSABLE',
            'AT_V2_MILEAGE_ENGINE_TEST_ENABLED', 'AT_V2_PRODUCTION_MILEAGE_ENABLED',
            'AT_V2_PROGRESSION_ENABLED'))
        and c.get('RAILWAY_PROJECT_ID') == '00313eef-3512-45ec-a4fa-fdcb815235a2'
        and c.get('RAILWAY_ENVIRONMENT_ID') == '72d029c5-98b4-4440-8492-8db12620d9da'
        and c.get('RAILWAY_SERVICE_ID') == '73f8817c-36be-423a-b68b-b457232cfcfd'
        and bool(c.get('AT_V2_LINKED_MILEAGE_OWNER_APPROVAL_REF', '').strip())
        and bool(re.fullmatch('[0-9a-f]{64}', c.get('AT_V2_LINKED_MILEAGE_DATABASE_SHA256', '')))
        and bool(re.fullmatch('[0-9a-f]{40}', c.get('RAILWAY_GIT_COMMIT_SHA', '')))
        and c.get('RAILWAY_GIT_COMMIT_SHA') == c.get('AT_V2_REGISTRATION_APPROVED_COMMIT')
    )


def vault_candidate(versions, driver_id):
    """Validate all authenticated versions, not a favourable latest version."""
    if not versions or any(v.get('source_kind') != 'authenticated_api'
                           or v.get('provider') != 'trucky' for v in versions):
        return None
    ids = {v.get('provider_job_id') for v in versions}
    if len(ids) != 1:
        return None
    job_id = next(iter(ids))
    if not isinstance(job_id, str) or not job_id.isascii() or not job_id.isdecimal() or int(job_id) <= 0:
        return None
    merged = merge_history_observations(versions)
    if merged is None:
        return None
    # Company provenance was checked before retention by the authenticated
    # company-49041 jobs adapter. These are reconstructed retained fields,
    # not a claim to have found a provider-supplied shared journey identifier.
    job = dict(merged['evidence'], id=int(job_id), company_id=49041,
               user_id=int(merged['provider_account_id']))
    candidate = normalize_verified_trucky_job(job,
        verified_provider_account_id=merged['provider_account_id'], discord_user_id=driver_id)
    if candidate is None:
        return None
    candidate['provider_account_id'] = merged['provider_account_id']
    candidate['miles'] = (candidate['kilometers'] * KM_TO_MILES).quantize(Decimal('.0000000001'))
    candidate['source_sha256'] = hashlib.sha256(json.dumps({
        'job': job_id, 'account': merged['provider_account_id'],
        'evidence': merged['evidence'], 'source': 'authenticated_api'
    }, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return candidate


async def _settle_candidate(conn, candidate, member_ids):
    """One transaction per job. Caller must have checked the runtime boundary."""
    user = candidate['discord_user_id']
    account = candidate['provider_account_id']
    job_id = candidate['provider_job_id']
    async with conn.transaction():
        # Shared with legacy TrucksBook writer; serializes first-row creation.
        await conn.execute('SELECT pg_advisory_xact_lock($1)', user)
        owner = await conn.fetchrow("""
            SELECT l.provider_account_id,s.steam_id64
            FROM unified_provider_links l
            JOIN unified_steam_links s USING(discord_user_id)
            JOIN unified_driver_profiles p USING(discord_user_id)
            JOIN unified_registration_sessions r USING(discord_user_id)
            WHERE l.provider='trucky' AND l.provider_account_id=$1
              AND l.discord_user_id=$2 AND s.verified_at IS NOT NULL
              AND p.status='active' AND r.status='approved'
            FOR SHARE OF l,s,p,r
        """, account, user)
        if owner is None or account not in member_ids:
            return 'account_link_pending'
        previous = await conn.fetchrow("""SELECT discord_user_id,provider_account_id,
            mileage_type,miles,source_evidence_sha256 FROM unified_v2_production_credit_ledger
            WHERE provider='trucky' AND provider_job_id=$1""", job_id)
        if previous:
            return 'replayed' if (
                previous['discord_user_id'] == user and previous['provider_account_id'] == account
                and previous['mileage_type'] == candidate['mileage_type']
                and previous['miles'] == candidate['miles']
                and previous['source_evidence_sha256'] == candidate['source_sha256']
            ) else 'conflicting_job_pending'
        totals = await conn.fetchrow("""SELECT combined_real_miles,trucky_race_miles
            FROM unified_v2_production_mileage_totals WHERE discord_user_id=$1 FOR UPDATE""", user)
        existing_real = totals['combined_real_miles'] if totals else Decimal(0)
        # This adapter owns only its own Trucky credits. Never combine with a
        # prior/full-cutover ledger or silently reinterpret its baseline.
        foreign_credit = await conn.fetchval("""SELECT EXISTS(SELECT 1
            FROM unified_v2_production_credit_ledger WHERE discord_user_id=$1
              AND canonical_credit_key NOT LIKE 'accepted-link-trucky:%')""", user)
        if foreign_credit:
            return 'ledger_cutover_pending'
        if candidate['mileage_type'] == 'real':
            overlap = await conn.fetchval("""SELECT
                EXISTS(SELECT 1 FROM unified_provider_links WHERE discord_user_id=$1 AND provider='trucksbook')
                OR EXISTS(SELECT 1 FROM driver_links WHERE discord_user_id=$1)
                OR EXISTS(SELECT 1 FROM processed_jobs WHERE discord_user_id=$1)
            """, user)
            legacy = await conn.fetchval('SELECT real_miles FROM driver_progress WHERE discord_user_id=$1 FOR UPDATE', user)
            # Even without a saved TB link, an existing unexplained balance is
            # historical evidence, never permission to add a second total.
            if overlap or int(legacy or 0) != int(existing_real.to_integral_value(rounding=ROUND_FLOOR)):
                await _record_decision(conn, candidate, 'pending_reconciliation', 'historical_or_trucksbook_overlap')
                return 'pending_reconciliation'
        ownership_digest = hashlib.sha256(json.dumps({
            'policy': POLICY, 'discord': user, 'account': account,
            'steam': owner['steam_id64'], 'active_company': 49041
        }, sort_keys=True).encode()).hexdigest()
        inserted = await conn.fetchval("""INSERT INTO unified_v2_production_credit_ledger
            (provider,provider_job_id,discord_user_id,provider_account_id,mileage_type,miles,
             canonical_credit_key,source_evidence_sha256,ownership_evidence_sha256)
            VALUES('trucky',$1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT DO NOTHING RETURNING provider_job_id""", job_id, user, account,
            candidate['mileage_type'], candidate['miles'], 'accepted-link-trucky:' + job_id,
            candidate['source_sha256'], ownership_digest)
        if inserted is None:
            return 'replayed'
        await conn.execute("""INSERT INTO unified_v2_production_mileage_baselines
            (discord_user_id,trucksbook_real_miles)
            VALUES($1,COALESCE((SELECT real_miles FROM driver_progress WHERE discord_user_id=$1),0))
            ON CONFLICT(discord_user_id) DO NOTHING""", user)
        real = candidate['miles'] if candidate['mileage_type'] == 'real' else Decimal(0)
        race = candidate['miles'] if candidate['mileage_type'] == 'race' else Decimal(0)
        await conn.execute("""INSERT INTO unified_v2_production_mileage_totals
            (discord_user_id,combined_real_miles,trucky_race_miles)
            VALUES($1,$2,$3) ON CONFLICT(discord_user_id) DO UPDATE SET
            combined_real_miles=unified_v2_production_mileage_totals.combined_real_miles+EXCLUDED.combined_real_miles,
            trucky_race_miles=unified_v2_production_mileage_totals.trucky_race_miles+EXCLUDED.trucky_race_miles,
            updated_at=NOW()""", user, real, race)
        if real:
            # Integer carry is derived from exact cumulative ledger amounts;
            # never round each individual job to legacy BIGINT precision.
            added = int((existing_real + real).to_integral_value(rounding=ROUND_FLOOR)) - int(existing_real.to_integral_value(rounding=ROUND_FLOOR))
            await conn.execute("""INSERT INTO driver_progress(discord_user_id,trucksbook_name,real_miles)
                VALUES($1,$2,$3) ON CONFLICT(discord_user_id) DO UPDATE SET
                real_miles=driver_progress.real_miles+EXCLUDED.real_miles,updated_at=NOW()""",
                user, 'Trucky:' + account, added)
        await _record_decision(conn, candidate, 'credited', POLICY)
        return 'credited_' + candidate['mileage_type']


async def _record_decision(conn, candidate, decision, reason):
    await conn.execute("""INSERT INTO unified_v2_production_mileage_decisions
        (provider,provider_job_id,discord_user_id,decision,reason_code,evidence_sha256)
        SELECT 'trucky',$1,$2,$3,$4,$5 WHERE NOT EXISTS(
            SELECT 1 FROM unified_v2_production_mileage_decisions WHERE provider='trucky'
             AND provider_job_id=$1 AND discord_user_id=$2 AND decision=$3
             AND reason_code=$4 AND evidence_sha256=$5)""",
        candidate['provider_job_id'], candidate['discord_user_id'], decision, reason, candidate['source_sha256'])


async def allocate_saved_linked_history(pool):
    """Reassociates old vault jobs automatically after registration; bounded credits.

    Unlinked jobs stay in the same durable vault, with separate Real/Race audit
    inventories. A failed roster fetch prevents credits, never deletes evidence.
    """
    if not activation_allowed():
        return {'status': 'disabled', 'mileage_awarded': False}
    from trucky_membership_adapter import fetch_current_member_ids
    members = await fetch_current_member_ids()
    async with pool.acquire() as conn:
        actual = await read_only_database_fingerprint(conn)
        if actual != os.environ.get('AT_V2_LINKED_MILEAGE_DATABASE_SHA256'):
            return {'status': 'database_identity_blocked', 'mileage_awarded': False}
        await conn.execute(SCHEMA)
        rows = await conn.fetch("""SELECT h.provider,h.provider_job_id,h.provider_account_id,
                h.source_kind,h.evidence,l.discord_user_id
            FROM pending_provider_job_history_v2 h
            JOIN unified_provider_links l ON l.provider=h.provider AND l.provider_account_id=h.provider_account_id
            WHERE h.provider='trucky' AND h.source_kind='authenticated_api'
            ORDER BY h.provider_job_id,h.payload_sha256""")
    groups = defaultdict(list)
    for row in rows:
        groups[row['provider_job_id']].append(dict(row))
    counts = defaultdict(int)
    credits = 0
    for versions in groups.values():
        users = {v['discord_user_id'] for v in versions}
        candidate = vault_candidate(versions, next(iter(users))) if len(users) == 1 else None
        if candidate is None:
            counts['invalid_or_conflicting_pending'] += 1
            continue
        # Inspect ALL observations, including any conflicting account that no
        # longer has a saved link. A join alone must not hide that conflict.
        async with pool.acquire() as conn:
            all_versions = await conn.fetch("""SELECT provider,provider_job_id,provider_account_id,source_kind,evidence
                FROM pending_provider_job_history_v2 WHERE provider='trucky'
                AND provider_job_id=$1 AND source_kind='authenticated_api'""", candidate['provider_job_id'])
            candidate = vault_candidate([dict(v) for v in all_versions], candidate['discord_user_id'])
            if candidate is None:
                counts['invalid_or_conflicting_pending'] += 1
                continue
            outcome = await _settle_candidate(conn, candidate, members)
        counts[outcome] += 1
        if outcome.startswith('credited_'):
            credits += 1
            if credits >= 100:
                break
    return {'status': 'linked_history_processed', 'counts': dict(counts),
            'mileage_awarded': credits > 0, 'registration_changed': False,
            'roles_changed': False, 'company_mods_unlocked': False}
