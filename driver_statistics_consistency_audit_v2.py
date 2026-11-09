"""Read-only driver totals and permanent-record consistency audit.

Requires a database connection supplied by the operator. Never repairs or
writes balances, achievements, roles or historical evidence.
"""

from decimal import Decimal


def summarize_driver_statistics(audit):
    """Summarize audit findings without hiding individual driver exceptions."""
    if audit["status"] != "audited":
        return {"status": audit["status"], "driver_count": 0,
                "affected_drivers": 0, "issue_counts": {},
                "missing_tables": audit.get("missing_tables", [])}
    counts = {}
    affected = 0
    for driver in audit["drivers"]:
        issues = set(driver["issues"])
        if issues:
            affected += 1
        for issue in issues:
            counts[issue] = counts.get(issue, 0) + 1
    return {"status": "audited", "driver_count": len(audit["drivers"]),
            "affected_drivers": affected, "issue_counts": counts,
            "missing_tables": []}


async def inspect_driver_statistics(conn):
    async with conn.transaction(readonly=True, isolation="repeatable_read"):
        required_tables = (
            "driver_progress",
            "unified_v2_production_mileage_baselines",
            "unified_v2_production_mileage_totals",
            "hall_of_fame",
        )
        missing = await conn.fetch("""
            SELECT name FROM unnest($1::text[]) AS required(name)
            WHERE to_regclass('public.' || name) IS NULL
            ORDER BY name
        """, list(required_tables))
        if missing:
            return {"status": "schema_missing",
                    "missing_tables": [row["name"] for row in missing],
                    "drivers": []}
        rows = await conn.fetch("""
            WITH driver_ids AS (
                SELECT discord_user_id FROM driver_progress
                UNION SELECT discord_user_id FROM unified_v2_production_mileage_baselines
                UNION SELECT discord_user_id FROM unified_v2_production_mileage_totals
                UNION SELECT discord_user_id FROM hall_of_fame
            )
            SELECT ids.discord_user_id,
                   p.real_miles AS legacy_real,
                   b.trucksbook_real_miles AS baseline_real,
                   COALESCE(t.combined_real_miles, 0) AS v2_real,
                   COALESCE(t.trucky_race_miles, 0) AS race_miles,
                   h.highest_verified_real_miles AS hall_real,
                   h.highest_tier AS hall_tier
            FROM driver_ids ids
            LEFT JOIN driver_progress p USING (discord_user_id)
            LEFT JOIN unified_v2_production_mileage_baselines b USING (discord_user_id)
            LEFT JOIN unified_v2_production_mileage_totals t USING (discord_user_id)
            LEFT JOIN hall_of_fame h USING (discord_user_id)
            ORDER BY p.discord_user_id
        """)
        results = []
        for row in rows:
            row = dict(row)
            expected = (int(Decimal(str(row["baseline_real"])) + Decimal(str(row["v2_real"])))
                        if row["baseline_real"] is not None else None)
            issues = []
            if expected is None:
                issues.append("baseline_missing")
            if row["legacy_real"] is None:
                issues.append("legacy_driver_progress_missing")
            elif expected is not None and expected != int(row["legacy_real"]):
                issues.append("legacy_vs_unified_real_mismatch")
            if (row["hall_real"] is not None
                    and row["legacy_real"] is not None
                    and int(row["hall_real"]) > int(row["legacy_real"])):
                # Hall of Fame is a permanent historical high-water mark.
                # Never downgrade it; surface the discrepancy for review.
                issues.append("hall_record_exceeds_current_real")
            results.append({
                "discord_user_id": row["discord_user_id"],
                "legacy_real": int(row["legacy_real"]) if row["legacy_real"] is not None else None,
                "expected_real": expected,
                "race_miles": str(row["race_miles"]),
                "hall_real": int(row["hall_real"]) if row["hall_real"] is not None else None,
                "issues": issues,
            })
        return {"status": "audited", "missing_tables": [], "drivers": results}


async def main():
    """Run the audit with a read-only database account; print counts only."""
    import asyncio
    import json
    import os
    import asyncpg

    database_url = os.environ.get("AT_AUDIT_READONLY_DATABASE_URL")
    if not database_url:
        raise RuntimeError("AT_AUDIT_READONLY_DATABASE_URL is required")
    conn = await asyncpg.connect(database_url, server_settings={
        "default_transaction_read_only": "on",
        "statement_timeout": "30000",
    })
    try:
        result = await inspect_driver_statistics(conn)
        print(json.dumps(summarize_driver_statistics(result), sort_keys=True))
    finally:
        await conn.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
