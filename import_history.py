import asyncio
import csv
import os

import asyncpg


DATABASE_URL = os.getenv("DATABASE_URL")
CSV_FILE = "trucksbook_history.csv"


# TrucksBook name -> Discord User ID
DRIVER_LINKS = {
    "Mystical Custom": int(os.getenv("DRIVER_MYSTICAL_CUSTOM")),
    "BUNGIE B": 1416143371319115837,
    "MR.HOBO": 1539275080134037504,
    "NXTCLUTCH": 1545451538728882301,
    "bran123": 1549476207215648879,
    "DannyAlpha": 1120815164091023390,
    "WILLIAM S.D016": 1009945499392020571,
    "Dancus15": 1134534113345413212,
}


def clean_number(value):
    if value is None:
        return 0

    value = str(value).strip()
    value = value.replace(",", "")
    value = value.replace(" ", "")

    try:
        return int(round(float(value)))
    except ValueError:
        return 0


async def main():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL has not been configured."
        )

    print("--------------------------------")
    print("A&T Transport Historical Import")
    print("--------------------------------")

    connection = await asyncpg.connect(DATABASE_URL)

    try:
        totals = {}
        imported_jobs = 0
        skipped_race_jobs = 0
        skipped_unknown_drivers = 0
        duplicate_jobs = 0

        # --------------------------------------------------
        # CREATE DRIVER LINKS
        # --------------------------------------------------

        for trucksbook_name, discord_user_id in DRIVER_LINKS.items():
            await connection.execute(
                """
                INSERT INTO driver_links (
                    trucksbook_name,
                    discord_user_id
                )
                VALUES ($1, $2)
                ON CONFLICT (trucksbook_name)
                DO UPDATE SET
                    discord_user_id = EXCLUDED.discord_user_id;
                """,
                trucksbook_name,
                discord_user_id,
            )

        print(
            f"Driver links ready: {len(DRIVER_LINKS)}"
        )

        # --------------------------------------------------
        # READ TRUCKSBOOK HISTORY
        # --------------------------------------------------

        with open(
            CSV_FILE,
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as csv_file:
            reader = csv.DictReader(csv_file)

            print("CSV columns:")
            print(reader.fieldnames)

            for row in reader:
                trucksbook_name = (
                    row.get("Name", "").strip()
                )

                if trucksbook_name not in DRIVER_LINKS:
                    skipped_unknown_drivers += 1
                    continue

                discord_user_id = DRIVER_LINKS[
                    trucksbook_name
                ]

                job_id = (
                    row.get("TrucksBookID", "").strip()
                )

                if not job_id:
                    continue

                accepted_distance = clean_number(
                    row.get("Accepted distance")
                )

                max_speed = clean_number(
                    row.get("Maximal reached speed")
                )

                # A&T Real mileage:
                # TrucksBook jobs at 62 MPH or below.
                if max_speed > 62:
                    skipped_race_jobs += 1
                    continue

                result = await connection.execute(
                    """
                    INSERT INTO processed_jobs (
                        job_id,
                        discord_user_id,
                        trucksbook_name,
                        accepted_distance,
                        statistics
                    )
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (job_id)
                    DO NOTHING;
                    """,
                    job_id,
                    discord_user_id,
                    trucksbook_name,
                    accepted_distance,
                    "Real",
                )

                if result == "INSERT 0 1":
                    imported_jobs += 1

                    totals[trucksbook_name] = (
                        totals.get(
                            trucksbook_name,
                            0,
                        )
                        + accepted_distance
                    )
                else:
                    duplicate_jobs += 1

        # --------------------------------------------------
        # BUILD DRIVER PROGRESS FROM PROCESSED JOBS
        # --------------------------------------------------

        for trucksbook_name, discord_user_id in DRIVER_LINKS.items():
            real_miles = await connection.fetchval(
                """
                SELECT COALESCE(
                    SUM(accepted_distance),
                    0
                )
                FROM processed_jobs
                WHERE discord_user_id = $1
                  AND statistics = 'Real';
                """,
                discord_user_id,
            )

            await connection.execute(
                """
                INSERT INTO driver_progress (
                    discord_user_id,
                    trucksbook_name,
                    real_miles,
                    updated_at
                )
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (discord_user_id)
                DO UPDATE SET
                    trucksbook_name =
                        EXCLUDED.trucksbook_name,
                    real_miles =
                        EXCLUDED.real_miles,
                    updated_at = NOW();
                """,
                discord_user_id,
                trucksbook_name,
                real_miles,
            )

        # --------------------------------------------------
        # VERIFY RESULTS
        # --------------------------------------------------

        rows = await connection.fetch(
            """
            SELECT
                trucksbook_name,
                real_miles
            FROM driver_progress
            ORDER BY real_miles DESC;
            """
        )

        print("--------------------------------")
        print("IMPORT COMPLETE")
        print("--------------------------------")

        print(
            f"New Real jobs imported: "
            f"{imported_jobs}"
        )
        print(
            f"Race jobs skipped: "
            f"{skipped_race_jobs}"
        )
        print(
            f"Duplicate jobs skipped: "
            f"{duplicate_jobs}"
        )
        print(
            f"Unknown drivers skipped: "
            f"{skipped_unknown_drivers}"
        )

        print("--------------------------------")
        print("A&T DRIVER MILEAGE")
        print("--------------------------------")

        for row in rows:
            print(
                f"{row['trucksbook_name']}: "
                f"{row['real_miles']:,} miles"
            )

        print("--------------------------------")
        print(
            "No Discord roles have been changed."
        )
        print("--------------------------------")

    finally:
        await connection.close()


asyncio.run(main())
