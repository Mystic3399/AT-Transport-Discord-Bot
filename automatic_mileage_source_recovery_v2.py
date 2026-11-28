"""Automatic source-history recovery gates: authenticated records only.

A Discord message by itself never proves a driver's tracking-account ownership.
Historical balances are not backfilled unless an independent snapshot proves the
job was posted after the previous balance was captured.
"""
from datetime import datetime, timedelta, timezone
import re

SCHEMA = """
CREATE TABLE IF NOT EXISTS automatic_mileage_channel_cursors_v2 (
  guild_id BIGINT NOT NULL, channel_id BIGINT NOT NULL,
  last_message_id BIGINT NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY(guild_id,channel_id)
);
"""

def trusted_account_id(value):
    return (isinstance(value, str) and bool(re.fullmatch(r"[1-9][0-9]{0,18}",value)))

def valid_replay_window(evidence, baseline_at, *, posted_after_minutes=120):
    """Never credit an archived message from the imported historical balance.

    Only a distinct, verified-source post after the saved snapshot is eligible
    for automatic review. Its current linked account and duplicate handling
    are still checked independently at credit time.
    """
    if not isinstance(evidence, dict) or evidence.get("unit") != "mi":
        return False
    if type(evidence.get("distance")) is not int or evidence["distance"] <= 0:
        return False
    if not isinstance(baseline_at, datetime) or baseline_at.tzinfo is None:
        return False
    raw = evidence.get("source_message_at")
    if not isinstance(raw, str) or len(raw) > 80:
        return False
    try:
        observed = datetime.fromisoformat(raw.replace("Z","+00:00"))
    except ValueError:
        return False
    if observed.tzinfo is None:
        return False
    now = datetime.now(timezone.utc)
    return (baseline_at + timedelta(minutes=posted_after_minutes) < observed <= now
            and observed > baseline_at.astimezone(timezone.utc))


def valid_temporal_trucksbook_observation(evidence, miles, *, expected_account_id=None):
    """Positive source integrity without pretending absent route/cargo exists."""
    from decimal import Decimal, InvalidOperation
    from world_of_trucks_policy_v2 import classify_event_mileage
    if not isinstance(evidence, dict) or evidence.get("unit") != "mi":
        return None
    if classify_event_mileage("trucksbook",evidence.get("stats_type")) is None:
        return None
    if type(evidence.get("distance")) is not int or evidence["distance"] <= 0:
        return None
    try:
        if Decimal(str(evidence["distance"])) != Decimal(str(miles)):
            return None
    except (ValueError,InvalidOperation,TypeError):
        return None
    value=evidence.get("source_message_at")
    if not isinstance(value,str) or len(value)>80:
        return None
    try:
        posted=datetime.fromisoformat(value.replace("Z","+00:00"))
    except ValueError:
        return None
    if posted.tzinfo is None or posted > datetime.now(timezone.utc):
        return None
    return posted


def temporally_independent(incoming_at, provider_post_at, *, minutes=120):
    """Conservatively exclude only well-separated completed journeys."""
    return (isinstance(incoming_at,datetime) and incoming_at.tzinfo is not None
            and isinstance(provider_post_at,datetime) and provider_post_at.tzinfo is not None
            and abs(incoming_at-provider_post_at) > timedelta(minutes=minutes))


async def current_trucksbook_member(account, report, fetch_profile, cache, *,
                                    now=None):
    """Affirm current A&T membership for an independently linked exact ID.

    A six-hour company snapshot can be stale. This short bounded lookup only
    resolves account/company membership; the caller independently verifies
    Steam/Discord ownership, credit ledger, duplicate jobs and Real policy.
    """
    if (not isinstance(report,dict) or report.get("discord") != "In server"
            or report.get("trucksbook_link_conflict")
            or not isinstance(account,str)
            or not account.isascii() or not account.isdecimal()
            or int(account)<=0):
        return False
    moment=now if now is not None else datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("Timezone-aware membership lookup timestamp required")
    cached=cache.get(account)
    if cached is not None:
        checked,member=cached
        if (isinstance(checked,datetime) and checked.tzinfo is not None
                and timedelta(0) <= moment-checked <
                    timedelta(seconds=120 if member else 600)):
            return bool(member)
    try:
        profile=await fetch_profile(int(account))
        member=(isinstance(profile,dict)
                and profile.get("success") is True
                and profile.get("belongs_to_at") is True)
    except Exception:
        member=False
    cache[account]=(moment,member)
    return member


async def safely_restore_accounted_trucksbook_history(conn, user, evidence, miles):
    """Allow old A&T webhook jobs only when every existing mile is explained.

    This is the TrucksBook-only driver recovery case: the source is retained
    from a configured A&T TrucksBook webhook, independently linked by exact
    provider account elsewhere, with present A&T membership. None of those
    identity facts may be inferred from a name. Called AGAIN under the
    per-driver writer lock, so concurrent jobs recheck the balance invariant.
    """
    if type(user) is not int or user<=0:
        return False
    if valid_temporal_trucksbook_observation(evidence, miles) is None:
        return False
    from decimal import Decimal, InvalidOperation
    facts=await conn.fetchrow("""
      SELECT COALESCE((SELECT real_miles FROM driver_progress
                       WHERE discord_user_id=$1),0) AS real_miles,
             COALESCE((SELECT SUM(accepted_distance) FROM processed_jobs
                       WHERE discord_user_id=$1
                         AND LOWER(statistics) LIKE 'real%'
                         AND accepted_distance>0),0) AS accounted_jobs_miles,
             (SELECT COUNT(*) FROM processed_jobs
              WHERE discord_user_id=$1 AND
                (LOWER(statistics) NOT LIKE 'real%' OR accepted_distance<=0))
                AS unsupported_prior_jobs,
             (SELECT COUNT(*) FROM unified_provider_links
              WHERE discord_user_id=$1 AND provider='trucky') AS trucky_links,
             (SELECT COUNT(*) FROM unified_v2_production_credit_ledger
              WHERE discord_user_id=$1) AS other_ledger_credits,
             COALESCE((SELECT combined_real_miles FROM
               unified_v2_production_mileage_totals WHERE discord_user_id=$1),0)
                 AS linked_combined_real,
             COALESCE((SELECT trucky_race_miles FROM
               unified_v2_production_mileage_totals WHERE discord_user_id=$1),0)
                 AS linked_race
    """,user)
    if not facts:
        return False
    if any(int(facts[key]) for key in (
            "unsupported_prior_jobs", "trucky_links", "other_ledger_credits")):
        return False
    try:
        real=Decimal(str(facts["real_miles"]))
        jobs=Decimal(str(facts["accounted_jobs_miles"]))
        combined=Decimal(str(facts["linked_combined_real"]))
        race=Decimal(str(facts["linked_race"]))
    except (TypeError,ValueError,InvalidOperation,KeyError):
        return False
    return (all(x.is_finite() and x>=0 for x in (real,jobs,combined,race))
            and real==jobs and combined==0 and race==0)
