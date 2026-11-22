"""Read-only explanation of why a named Discord driver's miles remain zero.

All source jobs are associated by immutable numeric tracking account IDs,
not display names.  This module cannot write driver links, mileage or ranks.
"""
from decimal import Decimal

async def inspect_driver(conn,discord_id):
    row=await conn.fetchrow("""
        SELECT
          (SELECT trucksbook_user_id::text FROM driver_links
           WHERE discord_user_id=$1) AS legacy_tb_id,
          (SELECT trucksbook_name FROM driver_links
           WHERE discord_user_id=$1) AS legacy_tb_name,
          (SELECT provider_account_id FROM unified_provider_links
           WHERE discord_user_id=$1 AND provider='trucksbook'
             AND verified_at IS NOT NULL) AS verified_tb_id,
          (SELECT provider_account_id FROM unified_provider_links
           WHERE discord_user_id=$1 AND provider='trucky'
             AND verified_at IS NOT NULL) AS verified_trucky_id,
          (SELECT trucksbook_user_id::text FROM driver_verifications
           WHERE discord_user_id=$1 AND status='completed'
             AND completed_at IS NOT NULL) AS completed_tb_id,
          (SELECT status FROM unified_provider_claims
           WHERE discord_user_id=$1 AND provider='trucksbook') AS ownership_claim_status,
          (SELECT ownership_confirmed FROM unified_provider_claims
           WHERE discord_user_id=$1 AND provider='trucksbook') AS ownership_confirmed,
          (SELECT ownership_evidence_kind FROM unified_provider_claims
           WHERE discord_user_id=$1 AND provider='trucksbook') AS ownership_evidence_kind,
          (SELECT reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL
           FROM unified_provider_claims
           WHERE discord_user_id=$1 AND provider='trucksbook') AS ownership_reviewed,
          (SELECT status FROM driver_verifications
           WHERE discord_user_id=$1) AS verification_status,
          COALESCE((SELECT real_miles FROM driver_progress
                    WHERE discord_user_id=$1),0) AS profile_real,
          (SELECT COUNT(*) FROM processed_jobs
           WHERE discord_user_id=$1 AND accepted_distance>0
             AND LOWER(statistics) LIKE 'real%') AS processed_real_jobs,
          COALESCE((SELECT SUM(accepted_distance) FROM processed_jobs
           WHERE discord_user_id=$1 AND accepted_distance>0
             AND LOWER(statistics) LIKE 'real%'),0) AS processed_real_miles,
          COALESCE((SELECT SUM(miles) FROM unified_v2_production_credit_ledger
           WHERE discord_user_id=$1 AND provider='trucky'
             AND mileage_type='real'),0) AS credited_trucky_real,
          COALESCE((SELECT SUM(miles) FROM unified_v2_production_credit_ledger
           WHERE discord_user_id=$1 AND provider='trucky'
             AND mileage_type='race'),0) AS credited_trucky_race
    """,discord_id)
    facts=dict(row or {})
    ids=[str(v) for k in ('legacy_tb_id','verified_tb_id','completed_tb_id')
         if (v:=facts.get(k)) is not None and str(v).isascii()
           and str(v).isdecimal() and int(v)>0]
    candidates=sorted(set(ids),key=int)
    facts['tb_accounts']=candidates
    facts['tb_account_conflict']=len(candidates)>1
    facts['numeric_tb_id_present']=bool(facts.get('legacy_tb_id') or facts.get('verified_tb_id'))
    if candidates:
        evidence=await conn.fetch("""
            SELECT h.provider_account_id,
                   COUNT(DISTINCT h.provider_job_id)::BIGINT AS source_jobs,
                   COUNT(DISTINCT h.provider_job_id) FILTER (
                     WHERE p.job_id IS NULL
                       AND LOWER(COALESCE(h.evidence->>'stats_type','')) LIKE 'real%'
                   )::BIGINT AS uncredited_real_sources,
                   COUNT(DISTINCT h.provider_job_id) FILTER (
                     WHERE p.job_id IS NOT NULL AND p.discord_user_id<>$2
                   )::BIGINT AS processed_to_other_driver
            FROM pending_provider_job_history_v2 h
            LEFT JOIN processed_jobs p ON p.job_id=h.provider_job_id
            WHERE h.provider='trucksbook' AND h.source_kind='verified_webhook_source'
              AND h.provider_account_id=ANY($1::text[])
            GROUP BY h.provider_account_id ORDER BY h.provider_account_id
        """,candidates,discord_id)
        holds=await conn.fetch("""
            SELECT provider_account_id,state,reason_code,COUNT(*)::BIGINT AS jobs
            FROM automatic_mileage_holding_v2
            WHERE provider='trucksbook'
              AND provider_account_id=ANY($1::text[])
              AND state<>'credited'
            GROUP BY provider_account_id,state,reason_code
            ORDER BY COUNT(*) DESC
        """,candidates)
    else:
        evidence=[]
        holds=[]
    facts['source_jobs_by_id']=[dict(x) for x in evidence]
    facts['holds_by_id']=[dict(x) for x in holds]
    return facts


def format_driver_report(member_id,facts,company_confirmed=None):
    linked=facts.get('tb_accounts') or []
    fmt=lambda value: f"{int(value or 0):,}"
    lines=[
        f"🔎 **A&T Driver Mileage — <@{member_id}>**",
        f"TrucksBook ID(s): **{', '.join(linked) if linked else 'Not saved/verified'}**",
        f"Saved numeric link: **{'Yes' if facts.get('numeric_tb_id_present') else 'No (name-only record)'}**",
        f"Registration verification: **{facts.get('verification_status') or 'Not recorded'}**",
        f"TrucksBook ownership claim: **{facts.get('ownership_claim_status') or 'Not recorded'}**",
        f"Independent ownership evidence: **{'Recorded (not automatically credit-authorising)' if facts.get('ownership_confirmed') and facts.get('ownership_reviewed') else 'Not established'}**",
        f"Real total on card: **{fmt(facts.get('profile_real'))} mi**",
        f"Processed TrucksBook Real/WoTr: **{fmt(facts.get('processed_real_jobs'))} jobs / {fmt(facts.get('processed_real_miles'))} mi**",
        f"Trucky credited: **{Decimal(str(facts.get('credited_trucky_real') or 0)):,.2f} Real / {Decimal(str(facts.get('credited_trucky_race') or 0)):,.2f} Race mi**",
    ]
    if company_confirmed is not None:
        lines.append("Current A&T TrucksBook membership: **" +
                     ("Verified" if company_confirmed else "Not confirmed") + "**")
    if facts.get('tb_account_conflict'):
        lines.append("⚠️ **Conflicting saved TrucksBook IDs: no automatic reassignment.**")
    for row in facts.get('source_jobs_by_id',[]):
        lines.append(f"Source history for ID {row['provider_account_id']}: "
                     f"**{fmt(row['source_jobs'])} captured / "
                     f"{fmt(row['uncredited_real_sources'])} uncredited Real job(s)**")
        if row.get('processed_to_other_driver'):
            lines.append(f"⚠️ **{fmt(row['processed_to_other_driver'])} job(s) attributed to another Discord driver.**")
    for row in facts.get('holds_by_id',[])[:4]:
        reason=str(row['reason_code']).replace('_',' ')
        lines.append(f"• Held: **{fmt(row['jobs'])}** — {reason}")
    if not linked:
        lines.append("Saved display names do not prove account ownership. "
                     "No mileage has been assigned to a guessed account.")
    elif not facts.get('source_jobs_by_id'):
        lines.append("No retained TrucksBook job source matched those exact numeric IDs. "
                     "The card alone cannot establish missing job mileage.")
    else:
        lines.append("Retained jobs still require the automatic duplicate and baseline checks. "
                     "Source-history counts are NOT approved missing miles.")
    lines.append("*Diagnostic only: no balances, links, jobs or roles changed.*")
    return "\n".join(lines)


async def scan_zero_mile_driver_sources(conn,limit=40,*,include_credited=False):
    """Read-only startup inventory of linked drivers whose cards show zero.

    The source count uses exact numeric provider IDs and does not reassign
    display-name matches. The result is diagnostic, not payable mileage.
    """
    rows=await conn.fetch("""
        WITH ids AS (
          SELECT discord_user_id FROM driver_links
          UNION SELECT discord_user_id FROM unified_provider_links
          WHERE provider='trucksbook'
        ), linked AS (
          SELECT ids.discord_user_id,
            COALESCE(d.trucksbook_name,p.provider_display_name) AS trucksbook_name,
            p.provider_account_id AS claimed_tb_id,
            (p.verified_at IS NOT NULL) AS claim_verified,
            proof.status AS ownership_claim_status,
            proof.ownership_confirmed AS ownership_confirmed,
            proof.membership_confirmed AS membership_confirmed,
            proof.ownership_evidence_kind AS ownership_evidence_kind,
            (proof.reviewed_at IS NOT NULL AND proof.reviewed_by IS NOT NULL)
              AS ownership_reviewed,
            COALESCE(d.trucksbook_user_id::text,
              CASE WHEN p.verified_at IS NOT NULL THEN p.provider_account_id END,
              CASE WHEN v.status='completed' AND v.completed_at IS NOT NULL
                   THEN v.trucksbook_user_id::text END) AS tb_id
          FROM ids
          LEFT JOIN driver_links d USING(discord_user_id)
          LEFT JOIN unified_provider_links p
            ON p.discord_user_id=ids.discord_user_id AND p.provider='trucksbook'
          LEFT JOIN driver_verifications v ON v.discord_user_id=ids.discord_user_id
          LEFT JOIN unified_provider_claims proof ON proof.discord_user_id=ids.discord_user_id
            AND proof.provider='trucksbook'
          WHERE (d.discord_user_id IS NULL
            OR d.trucksbook_name<>('__unlinked_driver_'||d.discord_user_id::text||'__'))
        )
        SELECT l.discord_user_id,l.trucksbook_name,l.tb_id,
               l.claimed_tb_id,l.claim_verified,
               l.ownership_claim_status,l.ownership_confirmed,
               l.membership_confirmed,l.ownership_evidence_kind,l.ownership_reviewed,
          COALESCE((SELECT real_miles FROM driver_progress
                    WHERE discord_user_id=l.discord_user_id),0) AS real_miles,
          (SELECT COUNT(*) FROM processed_jobs j
           WHERE j.discord_user_id=l.discord_user_id
             AND LOWER(j.statistics) LIKE 'real%') AS processed_jobs,
          (SELECT COUNT(DISTINCT h.provider_job_id)
           FROM pending_provider_job_history_v2 h
           WHERE h.provider='trucksbook'
             AND h.source_kind='verified_webhook_source'
             AND h.provider_account_id=COALESCE(l.tb_id,l.claimed_tb_id)) AS source_jobs,
          (SELECT COUNT(DISTINCT h.provider_job_id)
           FROM pending_provider_job_history_v2 h
           WHERE h.provider='trucksbook'
             AND h.source_kind='verified_webhook_source'
             AND h.provider_account_id=COALESCE(l.tb_id,l.claimed_tb_id)
             AND LOWER(COALESCE(h.evidence->>'stats_type','')) LIKE 'real%'
             AND NOT EXISTS(SELECT 1 FROM processed_jobs j
                            WHERE j.job_id=h.provider_job_id))
            AS uncredited_real_sources
        FROM linked l
        WHERE ($2::boolean OR COALESCE((SELECT real_miles FROM driver_progress
                        WHERE discord_user_id=l.discord_user_id),0)=0)
        ORDER BY uncredited_real_sources DESC,real_miles ASC,l.discord_user_id
        LIMIT $1
    """,limit,include_credited)
    return [dict(row) for row in rows]
