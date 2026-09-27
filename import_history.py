import asyncio
import csv
import os
import re

import asyncpg


# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL")
CSV_FILE = "trucksbook_history.csv"


# --------------------------------------------------
# TRUCKSBOOK -> DISCORD LINKS
# --------------------------------------------------

DRIVER_LINKS = {
    "Mystical Custom": int(
        os.getenv("DRIVER_MYSTICAL_CUSTOM")
    ),
    "BUNGIE B": 1416143371319115837,
    "MR.HOBO": 1539275080134037504,
    "NXTCLUTCH": 1545451538728882301,
    "bran123": 1549476207215648879,
    "DannyAlpha": 1120815164091023390,
    "WILLIAM S.D016": 1009945499392020571,
    "Dancus15": 1134534113345413212,
}


# --------------------------------------------------
# NUMBER PARSING
# --------------------------------------------------

def clean_number(value):
    """
    Converts TrucksBook values such as:

    175 mi
    60 mph
    1,234 mi
    62.4 mph

    into a numeric value.
    """

    if value is None:
        return 0

    value = str(value).strip()

    if not value:
        return 0

    match = re.search(
        r"-?[\d,]+(?:\.\d+)?",
        value,
    )

    if not match:
        return 0

    number = match.group(0).replace(",", "")

    try:
        return float(number)
    except ValueError:
        return 0


# --------------------------------------------------
# HISTORICAL IMPORT
# --------------------------------------------------

async def main():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL has not been configured."
        )

    print("--------------------------------")
    print("A&T Transport Historical Repair")
    print("--------------------------------")

    connection = await asyncpg.connect(
        DATABASE_URL
    )

    try:
        # ------------------------------------------
        # LOAD CSV FIRST
        # ------------------------------------------

        historical_rows = []

        with open(
            CSV_FILE,
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as csv_file:
            reader = csv.DictReader(
                csv_file,
                delimiter=";",
            )

            print("CSV columns:")
            print(reader.fieldnames)

            for row in reader:
                historical_rows.append(row)

        print(
            f"Historical CSV jobs found: "
            f"{len(historical_rows)}"
        )

        # ------------------------------------------
        # VALIDATE CSV BEFORE DATABASE CHANGES
        # ------------------------------------------

        if not historical_rows:
            raise RuntimeError(
                "Historical CSV contains no jobs."
            )

        required_columns = {
            "Name",
            "Accepted distance",
            "Maximal reached speed",
            "TrucksBookID",
        }

        csv_columns = set(
            historical_rows[0].keys()
        )

        missing_columns = (
            required_columns - csv_columns
        )

        if missing_columns:
            raise RuntimeError(
                "Missing CSV columns: "
                + ", ".join(
                    sorted(missing_columns)
                )
            )

        # ------------------------------------------
        # PREPARE HISTORICAL JOBS
        # ------------------------------------------

        qualifying_jobs = []
        race_jobs = []

        unknown_drivers = 0
        missing_job_ids = 0
        duplicate_csv_ids = 0

        seen_job_ids = set()

        for row in historical_rows:
            trucksbook_name = (
                row.get(
                    "Name",
                    "",
                ).strip()
            )

            if (
                trucksbook_name
                not in DRIVER_LINKS
            ):
                unknown_drivers += 1
                continue

            job_id = (
                row.get(
                    "TrucksBookID",
                    "",
                ).strip()
            )

            if not job_id:
                missing_job_ids += 1
                continue

            if job_id in seen_job_ids:
                duplicate_csv_ids += 1
                continue

            seen_job_ids.add(job_id)

            accepted_distance = clean_number(
                row.get(
                    "Accepted distance"
                )
            )

            max_speed = clean_number(
                row.get(
                    "Maximal reached speed"
                )
            )

            discord_user_id = (
                DRIVER_LINKS[
                    trucksbook_name
                ]
            )

            job_data = {
                "job_id": job_id,
                "discord_user_id":
                    discord_user_id,
                "trucksbook_name":
                    trucksbook_name,
                "accepted_distance":
                    int(round(
                        accepted_distance
                    )),
                "max_speed":
                    max_speed,
            }

            # TrucksBook historical CSV does not
            # include its Real/Race classification.
            #
            # A&T historical rule:
            # 62 MPH or below = qualifying Real job.

            if max_speed <= 62:
                qualifying_jobs.append(
                    job_data
                )
            else:
                race_jobs.append(
                    job_data
                )

        print("--------------------------------")
        print("CSV VALIDATION")
        print("--------------------------------")

        print(
            f"Qualifying Real jobs: "
            f"{len(qualifying_jobs)}"
        )

        print(
            f"Race jobs excluded: "
            f"{len(race_jobs)}"
        )

        print(
            f"Unknown drivers: "
            f"{unknown_drivers}"
        )

        print(
            f"Missing TrucksBook IDs: "
            f"{missing_job_ids}"
        )

        print(
            f"Duplicate CSV IDs: "
            f"{duplicate_csv_ids}"
        )

        # ------------------------------------------
        # SAFETY CHECKS
        # ------------------------------------------

        if unknown_drivers != 0:
            raise RuntimeError(
                "Import stopped because unknown "
                "drivers were found."
            )

        if missing_job_ids != 0:
            raise RuntimeError(
                "Import stopped because jobs with "
                "missing TrucksBook IDs were found."
            )

        if duplicate_csv_ids != 0:
            raise RuntimeError(
                "Import stopped because duplicate "
                "TrucksBook IDs were found."
            )

        # ------------------------------------------
        # DATABASE TRANSACTION
        # ------------------------------------------

        async with connection.transaction():

            # --------------------------------------
            # CREATE / UPDATE DRIVER LINKS
            # --------------------------------------

            for (
                trucksbook_name,
                discord_user_id,
            ) in DRIVER_LINKS.items():
                await connection.execute(
                    """
                    INSERT INTO driver_links (
                        trucksbook_name,
                        discord_user_id
                    )
                    VALUES ($1, $2)
                    ON CONFLICT (
                        trucksbook_name
                    )
                    DO UPDATE SET
                        discord_user_id =
                            EXCLUDED.discord_user_id;
                    """,
                    trucksbook_name,
                    discord_user_id,
                )

            print(
                f"Driver links ready: "
                f"{len(DRIVER_LINKS)}"
            )

            # --------------------------------------
            # REMOVE PREVIOUS HISTORICAL IMPORT
            # --------------------------------------

            all_csv_job_ids = list(
                seen_job_ids
            )

            deleted_jobs = 0

            if all_csv_job_ids:
                result = (
                    await connection.execute(
                        """
                        DELETE FROM processed_jobs
                        WHERE job_id =
                            ANY($1::text[]);
                        """,
                        all_csv_job_ids,
                    )
                )

                try:
                    deleted_jobs = int(
                        result.split()[-1]
                    )
                except (
                    ValueError,
                    IndexError,
                ):
                    deleted_jobs = 0

            print(
                f"Previous historical jobs "
                f"removed: {deleted_jobs}"
            )

            # --------------------------------------
            # INSERT CORRECT REAL JOBS
            # --------------------------------------

            inserted_jobs = 0

            for job in qualifying_jobs:
                result = (
                    await connection.execute(
                        """
                        INSERT INTO processed_jobs (
                            job_id,
                            discord_user_id,
                            trucksbook_name,
                            accepted_distance,
                            statistics
                        )
                        VALUES (
                            $1,
                            $2,
                            $3,
                            $4,
                            'Real'
                        )
                        ON CONFLICT (job_id)
                        DO NOTHING;
                        """,
                        job["job_id"],
                        job[
                            "discord_user_id"
                        ],
                        job[
                            "trucksbook_name"
                        ],
                        job[
                            "accepted_distance"
                        ],
                    )
                )

                if result == "INSERT 0 1":
                    inserted_jobs += 1

            # --------------------------------------
            # REBUILD DRIVER PROGRESS
            # --------------------------------------

            for (
                trucksbook_name,
                discord_user_id,
            ) in DRIVER_LINKS.items():

                real_miles = (
                    await connection.fetchval(
                        """
                        SELECT COALESCE(
                            SUM(
                                accepted_distance
                            ),
                            0
                        )
                        FROM processed_jobs
                        WHERE discord_user_id = $1
                          AND statistics = 'Real';
                        """,
                        discord_user_id,
                    )
                )

                await connection.execute(
                    """
                    INSERT INTO driver_progress (
                        discord_user_id,
                        trucksbook_name,
                        real_miles,
                        updated_at
                    )
                    VALUES (
                        $1,
                        $2,
                        $3,
                        NOW()
                    )
                    ON CONFLICT (
                        discord_user_id
                    )
                    DO UPDATE SET
                        trucksbook_name =
                            EXCLUDED.trucksbook_name,
                        real_miles =
                            EXCLUDED.real_miles,
                        updated_at =
                            NOW();
                    """,
                    discord_user_id,
                    trucksbook_name,
                    real_miles,
                )

        # ------------------------------------------
        # VERIFY FINAL DATABASE TOTALS
        # ------------------------------------------

        rows = await connection.fetch(
            """
            SELECT
                trucksbook_name,
                real_miles
            FROM driver_progress
            ORDER BY real_miles DESC;
            """
        )

        processed_job_count = (
            await connection.fetchval(
                """
                SELECT COUNT(*)
                FROM processed_jobs
                WHERE statistics = 'Real';
                """
            )
        )

        print("--------------------------------")
        print("HISTORICAL REPAIR COMPLETE")
        print("--------------------------------")

        print(
            f"Correct Real jobs inserted: "
            f"{inserted_jobs}"
        )

        print(
            f"Race jobs excluded: "
            f"{len(race_jobs)}"
        )

        print(
            f"Real jobs currently stored: "
            f"{processed_job_count}"
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


# --------------------------------------------------
# START IMPORT
# --------------------------------------------------

if __name__ == "__main__":
    asyncio.run(main())
