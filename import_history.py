import asyncio
import os

import asyncpg


# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL")


# --------------------------------------------------
# AUTHORITATIVE TRUCKSBOOK TOTALS
# --------------------------------------------------
# Source:
# A&T Transport LTD -> Log Overview -> User summaries
#
# These are the current Accepted Distance totals
# shown by TrucksBook.
# --------------------------------------------------

DRIVER_TOTALS = {
    "Mystical Custom": {
        "discord_user_id": int(
            os.getenv("DRIVER_MYSTICAL_CUSTOM")
        ),
        "real_miles": 40372,
    },

    "BUNGIE B": {
        "discord_user_id": 1416143371319115837,
        "real_miles": 33859,
    },

    "MR.HOBO": {
        "discord_user_id": 1539275080134037504,
        "real_miles": 22212,
    },

    "NXTCLUTCH": {
        "discord_user_id": 1545451538728882301,
        "real_miles": 3567,
    },

    "DannyAlpha": {
        "discord_user_id": 1120815164091023390,
        "real_miles": 3544,
    },

    "bran123": {
        "discord_user_id": 1549476207215648879,
        "real_miles": 1584,
    },

    "Dancus15": {
        "discord_user_id": 1134534113345413212,
        "real_miles": 1463,
    },

    "WILLIAM S.D016": {
        "discord_user_id": 1009945499392020571,
        "real_miles": 242,
    },
}


# --------------------------------------------------
# EXPECTED TOTAL
# --------------------------------------------------

EXPECTED_COMPANY_TOTAL = 106842


# --------------------------------------------------
# MAIN REPAIR
# --------------------------------------------------

async def main():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL has not been configured."
        )

    print("--------------------------------")
    print("A&T Transport Baseline Repair")
    print("--------------------------------")

    calculated_total = sum(
        driver["real_miles"]
        for driver in DRIVER_TOTALS.values()
    )

    print(
        f"Drivers: {len(DRIVER_TOTALS)}"
    )

    print(
        f"Calculated company total: "
        f"{calculated_total:,} miles"
    )

    print(
        f"Expected TrucksBook total: "
        f"{EXPECTED_COMPANY_TOTAL:,} miles"
    )

    if calculated_total != EXPECTED_COMPANY_TOTAL:
        raise RuntimeError(
            "Safety check failed: driver totals "
            "do not equal the TrucksBook company total."
        )

    print(
        "Safety check passed."
    )

    connection = await asyncpg.connect(
        DATABASE_URL
    )

    try:
        async with connection.transaction():

            # --------------------------------------
            # UPDATE DRIVER LINKS
            # --------------------------------------

            for (
                trucksbook_name,
                driver_data,
            ) in DRIVER_TOTALS.items():

                discord_user_id = (
                    driver_data[
                        "discord_user_id"
                    ]
                )

                await connection.execute(
                    """
                    INSERT INTO driver_links (
                        trucksbook_name,
                        discord_user_id
                    )
                    VALUES (
                        $1,
                        $2
                    )
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
                "Driver links updated."
            )

            # --------------------------------------
            # SET AUTHORITATIVE MILEAGE TOTALS
            # --------------------------------------

            for (
                trucksbook_name,
                driver_data,
            ) in DRIVER_TOTALS.items():

                discord_user_id = (
                    driver_data[
                        "discord_user_id"
                    ]
                )

                real_miles = (
                    driver_data[
                        "real_miles"
                    ]
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

            print(
                "Driver mileage totals updated."
            )

        # ------------------------------------------
        # VERIFY DATABASE
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

        database_total = sum(
            int(row["real_miles"])
            for row in rows
        )

        print("--------------------------------")
        print("DATABASE VERIFICATION")
        print("--------------------------------")

        for row in rows:
            print(
                f"{row['trucksbook_name']}: "
                f"{row['real_miles']:,} miles"
            )

        print("--------------------------------")

        print(
            f"Database company total: "
            f"{database_total:,} miles"
        )

        if database_total == EXPECTED_COMPANY_TOTAL:
            print(
                "DATABASE TOTAL VERIFIED"
            )
        else:
            print(
                "WARNING: Database total does "
                "not match TrucksBook."
            )

        print("--------------------------------")
        print("BASELINE REPAIR COMPLETE")
        print("--------------------------------")
        print(
            "No Discord roles have been changed "
            "by this importer."
        )
        print("--------------------------------")

    finally:
        await connection.close()


# --------------------------------------------------
# START
# --------------------------------------------------

if __name__ == "__main__":
    asyncio.run(main())
