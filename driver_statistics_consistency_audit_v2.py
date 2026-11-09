"""Read-only driver totals and permanent-record consistency audit.

Requires a database connection supplied by the operator. Never repairs or
writes balances, achievements, roles or historical evidence.
"""

from decimal import Decimal


async def inspect_driver_statistics(conn):
    async with conn.transaction(readonly=True, isolation="repeatable_read"):
        rows = await conn.fetch("""
            SELECT p.discord_user_id,
                   p.real_miles AS legacy_real,
                   b.trucksbook_real_miles AS baseline_real,
                   COALESCE(t.combined_real_miles, 0) AS v2_real,
                   COALESCE(t.trucky_race_miles, 0) AS race_miles,
                   h.highest_verified_real_miles AS hall_real,
                   h.highest_tier AS hall_tier
            FROM driver_progress p
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
            elif expected != int(row["legacy_real"]):
                issues.append("legacy_vs_unified_real_mismatch")
            if (row["hall_real"] is not None
                    and int(row["hall_real"]) > int(row["legacy_real"])):
                # Hall of Fame is a permanent historical high-water mark.
                # Never downgrade it; surface the discrepancy for review.
                issues.append("hall_record_exceeds_current_real")
            results.append({
                "discord_user_id": row["discord_user_id"],
                "legacy_real": int(row["legacy_real"]),
                "expected_real": expected,
                "race_miles": str(row["race_miles"]),
                "hall_real": int(row["hall_real"]) if row["hall_real"] is not None else None,
                "issues": issues,
            })
        return results
