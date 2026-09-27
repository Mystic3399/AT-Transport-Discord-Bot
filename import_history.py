import asyncio
import os

import asyncpg


# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL")


# --------------------------------------------------
# AUTHORITATIVE TRUCKSBOOK DRIVER TOTALS
# --------------------------------------------------
#
# Source:
# A&T Transport LTD
# Log Overview -> User summaries
#
# These individual displayed totals are used for
# Discord progression.
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
# TRUCKSBOOK DISPLAY TOTAL
# --------------------------------------------------
#
# TrucksBook displays 106,842 miles as the company
# total.
#
# Its eight displayed driver totals add up to
# 106,843 miles.
#
# This 1-mile difference is treated as a display /
# rounding discrepancy. Individual driver totals
# are authoritative for progression roles.
# --------------------------------------------------

TRUCKSBOOK_COMPANY_DISPLAY_TOTAL = 106842

EXPECTED_DRIVER_TOTAL = 106843


# --------------------------------------------------
# EXPECTED DRIVER VALUES
# --------------------------------------------------

EXPECTED_DRIVER_MILES = {
    "Mystical Custom": 40372,
    "BUNGIE B": 33859,
    "MR.HOBO": 22212,
    "NXTCLUTCH": 3567,
    "DannyAlpha": 3544,
    "bran123": 1584,
    "Dancus15": 1463,
    "WILLIAM S.D016": 242,
}


# --------------------------------------------------
# MAIN BASELINE REPAIR
# --------------------------------------------------

async def main():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL has not been configured."
        )

    print("--------------------------------")
    print("A&T Transport Baseline Repair")
    print("--------------------------------")

    # ----------------------------------------------
    # SAFETY CHECK 1
    # VERIFY ALL EIGHT DRIVERS
    # ----------------------------------------------

    if len(DRIVER_TOTALS) != 8:
        raise RuntimeError(
            "Safety check failed: "
            "expected exactly 8 drivers."
        )

    print("Drivers: 8")

    # ----------------------------------------------
    # SAFETY CHECK 2
    # VERIFY EACH DRIVER'S MILEAGE
    # ----------------------------------------------

    for (
        trucksbook_name,
        expected_miles,
    ) in EXPECTED_DRIVER_MILES.items():

        if (
            trucksbook_name
            not in DRIVER_TOTALS
        ):
            raise RuntimeError(
                "Safety check failed: "
                f"{trucksbook_name} is missing."
            )

        actual_miles = (
            DRIVER_TOTALS[
                trucksbook_name
            ]["real_miles"]
        )

        if actual_miles != expected_miles:
            raise RuntimeError(
                "Safety check failed for "
                f"{trucksbook_name}. "
                f"Expected {expected_miles:,}, "
                f"found {actual_miles:,}."
            )

    print(
        "Individual driver totals verified."
    )

    # ----------------------------------------------
    # SAFETY CHECK 3
    # VERIFY SUM OF DISPLAYED DRIVER TOTALS
    # ----------------------------------------------

    calculated_driver_total = sum(
        driver["real_miles"]
        for driver in DRIVER_TOTALS.values()
    )

    print(
        "Calculated driver total: "
        f"{calculated_driver_total:,} miles"
    )

    if (
        calculated_driver_total
        != EXPECTED_DRIVER_TOTAL
    ):
        raise RuntimeError(
            "Safety check failed: "
            "displayed driver totals have changed."
        )

    print(
        "Expected driver total: "
        f"{EXPECTED_DRIVER_TOTAL:,} miles"
    )

    # ----------------------------------------------
    # REPORT TRUCKSBOOK ROUNDING DIFFERENCE
    # ----------------------------------------------

    difference = (
        calculated_driver_total
        - TRUCKSBOOK_COMPANY_DISPLAY_TOTAL
    )

    print(
        "TrucksBook company display total: "
        f"{TRUCKSBOOK_COMPANY_DISPLAY_TOTAL:,} miles"
    )

    print(
        "Displayed total difference: "
        f"{difference:+,} mile"
    )

    print(
        "Safety checks passed."
    )

    # ----------------------------------------------
    # CONNECT TO POSTGRESQL
    # ----------------------------------------------

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
            # SET AUTHORITATIVE MILEAGE
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
        # READ DATABASE BACK
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

        print("--------------------------------")
        print("DATABASE VERIFICATION")
        print("--------------------------------")

        database_values = {}

        for row in rows:
            trucksbook_name = (
                row["trucksbook_name"]
            )

            real_miles = int(
                row["real_miles"]
            )

            database_values[
                trucksbook_name
            ] = real_miles

            print(
                f"{trucksbook_name}: "
                f"{real_miles:,} miles"
            )

        # ------------------------------------------
        # VERIFY EACH DATABASE VALUE
        # ------------------------------------------

        for (
            trucksbook_name,
            expected_miles,
        ) in EXPECTED_DRIVER_MILES.items():

            database_miles = (
                database_values.get(
                    trucksbook_name
                )
            )

            if database_miles != expected_miles:
                raise RuntimeError(
                    "Database verification "
                    "failed for "
                    f"{trucksbook_name}."
                )

        database_total = sum(
            database_values.values()
        )

        print("--------------------------------")

        print(
            "Database driver total: "
            f"{database_total:,} miles"
        )

        if database_total != EXPECTED_DRIVER_TOTAL:
            raise RuntimeError(
                "Database total verification "
                "failed."
            )

        print(
            "DATABASE DRIVER TOTALS VERIFIED"
        )

        print("--------------------------------")
        print("BASELINE REPAIR COMPLETE")
        print("--------------------------------")

        print(
            "Discord roles were not changed "
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
