"""Read-only snapshot of A&T mileage state for historical reconciliation.

Run with DATABASE_URL in a trusted environment. Never commit output: it contains
Discord IDs and driver records. This script makes no database writes.
"""
import argparse
import asyncio
import csv
import os
from pathlib import Path

import asyncpg


async def snapshot(destination: Path):
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not configured")
    conn = await asyncpg.connect(url)
    try:
        async with conn.transaction(readonly=True):
            progress = await conn.fetch(
                "SELECT discord_user_id, trucksbook_name, real_miles, updated_at "
                "FROM driver_progress ORDER BY discord_user_id"
            )
            jobs = await conn.fetch(
                "SELECT job_id, discord_user_id, trucksbook_name, accepted_distance, "
                "statistics, processed_at FROM processed_jobs ORDER BY job_id"
            )
            verification_exists = await conn.fetchval(
                "SELECT to_regclass('public.driver_verifications') IS NOT NULL"
            )
            print(f"Driver records: {len(progress)}; processed jobs: {len(jobs)}")
            destination.mkdir(parents=True, exist_ok=True)
            write_csv(destination / "driver_progress.csv",
                      ["discord_user_id", "trucksbook_name", "real_miles", "updated_at"], progress)
            write_csv(destination / "processed_jobs.csv",
                      ["job_id", "discord_user_id", "trucksbook_name",
                       "accepted_distance", "statistics", "processed_at"], jobs)
            if verification_exists:
                print("Driver verification table present; inspect identity schema before joining.")
    finally:
        await conn.close()


def write_csv(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(fields)
        for row in rows:
            writer.writerow([str(row[field]) if row[field] is not None else "" for field in fields])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="private-mileage-audit")
    args = parser.parse_args()
    asyncio.run(snapshot(Path(args.output)))
