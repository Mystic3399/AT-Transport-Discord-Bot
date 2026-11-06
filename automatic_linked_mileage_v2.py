"""Per-job reconciliation within the existing per-driver credit transaction."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json

from unified_job_matching_v2 import is_candidate_duplicate, _normalized_metadata

SCHEMA = """
CREATE TABLE IF NOT EXISTS automatic_linked_journey_pairs_v2 (
 trucky_job_id TEXT PRIMARY KEY,
 trucksbook_job_id TEXT NOT NULL UNIQUE,
 discord_user_id BIGINT NOT NULL,
 winner TEXT NOT NULL CHECK(winner IN ('trucky','trucksbook')),
 trucky_miles NUMERIC(24,10) NOT NULL CHECK(trucky_miles>0),
 trucksbook_miles NUMERIC(24,10) NOT NULL CHECK(trucksbook_miles>0),
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def trucksbook_comparison(job_id, user, evidence, miles):
    value = json.loads(evidence) if isinstance(evidence, str) else evidence
    if value.get("unit") != "mi":
        raise ValueError("Explicit miles required")
    record = dict(provider="trucksbook", provider_job_id=job_id, discord_user_id=user,
                  mileage_type="real", miles=Decimal(str(miles)), metadata=value,
                  delivered_at=datetime.fromisoformat(value["source_message_at"]))
    _normalized_metadata(value)
    if record["delivered_at"].tzinfo is None or record["miles"] <= 0:
        raise ValueError("Invalid delivery")
    return record


def matches(first, second):
    return is_candidate_duplicate(first, second, max_miles_delta=None)


async def mark_suppressed(conn, provider, job_id, user, reason):
    digest = hashlib.sha256(f"{provider}:{job_id}:{user}:{reason}".encode()).hexdigest()
    await conn.execute("""
        INSERT INTO unified_v2_production_mileage_decisions
            (provider,provider_job_id,discord_user_id,decision,reason_code,evidence_sha256)
        SELECT $1,$2,$3,'duplicate_provider_job',$4,$5 WHERE NOT EXISTS (
            SELECT 1 FROM unified_v2_production_mileage_decisions
            WHERE provider=$1 AND provider_job_id=$2 AND discord_user_id=$3
              AND decision='duplicate_provider_job' AND reason_code=$4)
        """, provider, job_id, user, reason, digest)


async def reconcile_trucky(conn, candidate):
    """Return the credited TrucksBook amount being replaced, or a hold reason."""
    user = candidate["discord_user_id"]
    paired = await conn.fetchrow(
        "SELECT * FROM automatic_linked_journey_pairs_v2 WHERE trucky_job_id=$1",
        candidate["provider_job_id"])
    if paired:
        return None, "duplicate_provider_job"
    if not candidate.get("job_metadata"):
        return None, "route_evidence_pending"
    incoming = dict(candidate, metadata=candidate["job_metadata"])
    rows = await conn.fetch("""
        SELECT p.job_id,p.accepted_distance,h.evidence
        FROM processed_jobs p JOIN pending_provider_job_history_v2 h
          ON h.provider='trucksbook' AND h.provider_job_id=p.job_id
          AND h.source_kind='verified_webhook_source'
        WHERE p.discord_user_id=$1 AND LOWER(p.statistics)='real'
          AND p.accepted_distance>0
        """, user)
    by_job = {}
    for row in rows:
        by_job.setdefault(row["job_id"], []).append(row)
    found = []
    for job_id, versions in by_job.items():
        comparisons = []
        for version in versions:
            try:
                comparisons.append(trucksbook_comparison(
                    job_id, user, version["evidence"], version["accepted_distance"]))
            except (ValueError, TypeError, KeyError):
                comparisons = []
                break
        if comparisons and any(matches(incoming, item) for item in comparisons):
            if not all(matches(incoming, item) for item in comparisons):
                return None, "conflicting_route_evidence"
            found.append(comparisons[0])
    if len(found) > 1:
        return None, "ambiguous_journey"
    if found:
        other = found[0]
        peers = await linked_trucky_comparisons(conn, user)
        if sum(matches(peer, other) for peer in peers) != 1:
            return None, "ambiguous_journey"
        # A TrucksBook job already paired to a different Trucky delivery cannot
        # be reused even when the route happens to repeat.
        if await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM automatic_linked_journey_pairs_v2 WHERE trucksbook_job_id=$1)",
                other["provider_job_id"]):
            return None, "ambiguous_journey"
        winner = "trucky" if candidate["miles"] > other["miles"] else "trucksbook"
        await conn.execute("""
            INSERT INTO automatic_linked_journey_pairs_v2
                (trucky_job_id,trucksbook_job_id,discord_user_id,winner,trucky_miles,trucksbook_miles)
            VALUES($1,$2,$3,$4,$5,$6)
            """, candidate["provider_job_id"], other["provider_job_id"], user,
            winner, candidate["miles"], other["miles"])
        if winner == "trucksbook":
            await mark_suppressed(conn, "trucky", candidate["provider_job_id"], user,
                                  "lower_or_equal_real_credit")
            return None, "duplicate_provider_job"
        await mark_suppressed(conn, "trucksbook", other["provider_job_id"], user,
                              "superseded_by_higher_real_credit")
        return other["miles"], None
    # Missing historical balance is preserved. Only deliveries after its saved
    # snapshot can be proved absent from that historical balance.
    baseline = await conn.fetchrow("""
        SELECT b.snapshotted_at,
               COALESCE((SELECT real_miles FROM driver_progress WHERE discord_user_id=$1),0) AS total,
               COALESCE((SELECT SUM(accepted_distance) FROM processed_jobs
                   WHERE discord_user_id=$1 AND LOWER(statistics)='real'
                     AND NOT EXISTS (SELECT 1 FROM unified_v2_production_mileage_decisions d
                         WHERE d.provider='trucksbook' AND d.provider_job_id=processed_jobs.job_id
                           AND d.discord_user_id=$1 AND d.decision='duplicate_provider_job'
                           AND d.reason_code='superseded_by_higher_real_credit')),0) AS tb_jobs,
               COALESCE((SELECT SUM(miles) FROM unified_v2_production_credit_ledger
                   WHERE discord_user_id=$1 AND provider='trucky' AND mileage_type='real'),0) AS tr_jobs
        FROM (SELECT $1::bigint AS id) u
        LEFT JOIN unified_v2_production_mileage_baselines b ON b.discord_user_id=u.id
        """, user)
    processed_count = await conn.fetchval(
        "SELECT COUNT(*) FROM processed_jobs WHERE discord_user_id=$1 AND LOWER(statistics)='real' AND accepted_distance>0",
        user)
    if processed_count != len(by_job) and (
            baseline["snapshotted_at"] is None or candidate["delivered_at"] <= baseline["snapshotted_at"]):
        return None, "historical_route_coverage_pending"
    accounted = Decimal(baseline["tb_jobs"]) + Decimal(baseline["tr_jobs"]).to_integral_value(rounding="ROUND_FLOOR")
    if Decimal(baseline["total"]) != accounted and (
            baseline["snapshotted_at"] is None or candidate["delivered_at"] <= baseline["snapshotted_at"]):
        return None, "historical_balance_pending"
    if candidate["delivered_at"] > datetime.now(timezone.utc) - timedelta(minutes=30):
        return None, "delivery_window_pending"
    return Decimal(0), None


async def linked_trucky_comparisons(conn, user):
    from collections import defaultdict
    from trucky_linked_allocation_v2 import vault_candidate
    from unified_production_ledger_adapter_v2 import comparison_metadata_from_history

    rows = await conn.fetch("""
        SELECT h.provider,h.provider_job_id,h.provider_account_id,h.source_kind,h.evidence
        FROM pending_provider_job_history_v2 h
        WHERE h.provider='trucky' AND h.source_kind='authenticated_api'
          AND EXISTS (SELECT 1 FROM unified_provider_links l WHERE l.provider='trucky'
              AND l.provider_account_id=h.provider_account_id AND l.discord_user_id=$1)
        """, user)
    groups = defaultdict(list)
    for row in rows:
        groups[row["provider_job_id"]].append(dict(row))
    result = []
    for versions in groups.values():
        candidate = vault_candidate(versions, user)
        if candidate and candidate["mileage_type"] == "real":
            metadata = comparison_metadata_from_history(
                candidate, dict(mileage_type="real", canonical_miles=str(candidate["miles"])), versions)
            if metadata:
                result.append(dict(candidate, metadata=metadata))
    return result


async def reconcile_trucksbook(conn, job_id, user, miles):
    """Return net legacy change for a new TB job, or a reason to hold it."""
    from decimal import ROUND_FLOOR
    rows = await conn.fetch("""
        SELECT evidence FROM pending_provider_job_history_v2
        WHERE provider='trucksbook' AND provider_job_id=$1
          AND source_kind='verified_webhook_source'
        """, job_id)
    incoming_versions = []
    try:
        for row in rows:
            incoming_versions.append(trucksbook_comparison(job_id, user, row["evidence"], miles))
    except (ValueError, TypeError, KeyError):
        return None, "route_evidence_pending"
    if not incoming_versions:
        return None, "route_evidence_pending"
    credits = await conn.fetch("""
        SELECT provider_job_id,miles FROM unified_v2_production_credit_ledger
        WHERE discord_user_id=$1 AND provider='trucky' AND mileage_type='real'
        """, user)
    credited = {row["provider_job_id"]: row["miles"] for row in credits}
    candidates = await linked_trucky_comparisons(conn, user)
    found = [candidate for candidate in candidates
             if any(matches(candidate, item) for item in incoming_versions)]
    if len(found) > 1:
        return None, "ambiguous_journey"
    if not found:
        # Every existing credit needs comparison metadata before absence can
        # be established. A missing old route must never become a second award.
        if not set(credited).issubset({candidate["provider_job_id"] for candidate in candidates}):
            return None, "route_evidence_pending"
        return Decimal(miles), None
    candidate = found[0]
    if not all(matches(candidate, item) for item in incoming_versions):
        return None, "conflicting_route_evidence"
    pair = await conn.fetchrow("""
        SELECT * FROM automatic_linked_journey_pairs_v2
        WHERE trucky_job_id=$1 OR trucksbook_job_id=$2
        """, candidate["provider_job_id"], job_id)
    if pair:
        if pair["trucksbook_job_id"] != job_id or pair["trucky_job_id"] != candidate["provider_job_id"]:
            return None, "ambiguous_journey"
        return None, "duplicate_provider_job"
    # An uncredited Trucky counterpart will be handled by the vault worker
    # after this ordinary TrucksBook job has been committed.
    if candidate["provider_job_id"] not in credited:
        return Decimal(miles), None
    amount = credited[candidate["provider_job_id"]]
    winner = "trucky" if amount > Decimal(miles) else "trucksbook"
    await conn.execute("""
        INSERT INTO automatic_linked_journey_pairs_v2
            (trucky_job_id,trucksbook_job_id,discord_user_id,winner,trucky_miles,trucksbook_miles)
        VALUES($1,$2,$3,$4,$5,$6)
        """, candidate["provider_job_id"], job_id, user, winner, amount, Decimal(miles))
    if winner == "trucky":
        await mark_suppressed(conn, "trucksbook", job_id, user, "superseded_by_higher_real_credit")
        return Decimal(0), None
    before = await conn.fetchval("""
        SELECT combined_real_miles FROM unified_v2_production_mileage_totals
        WHERE discord_user_id=$1 FOR UPDATE
        """, user)
    if before is None or before < amount:
        raise ValueError("Trucky totals inconsistent with credit ledger")
    await conn.execute("""
        DELETE FROM unified_v2_production_credit_ledger
        WHERE provider='trucky' AND provider_job_id=$1 AND discord_user_id=$2
        """, candidate["provider_job_id"], user)
    await conn.execute("""
        UPDATE unified_v2_production_mileage_totals
        SET combined_real_miles=combined_real_miles-$2,updated_at=NOW()
        WHERE discord_user_id=$1
        """, user, amount)
    await mark_suppressed(conn, "trucky", candidate["provider_job_id"], user,
                          "superseded_by_higher_real_credit")
    removed = before.to_integral_value(rounding=ROUND_FLOOR) - (before-amount).to_integral_value(rounding=ROUND_FLOOR)
    return Decimal(miles)-removed, None
