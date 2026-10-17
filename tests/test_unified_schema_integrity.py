"""Regression checks for unified V2 schema integrity (no database needed)."""
import ast
import unittest
import unified_driver_profiles as profiles


class SchemaIntegrityTests(unittest.TestCase):
    def test_all_identity_tables_present_once(self):
        for table in (
            "unified_driver_profiles",
            "unified_steam_links",
            "unified_steam_auth_states",
            "unified_steam_openid_nonces",
            "unified_provider_links",
            "unified_registration_sessions",
            "unified_provider_claims",
            "unified_provider_job_events",
            "unified_job_matches",
        ):
            self.assertEqual(
                profiles.SCHEMA.count("CREATE TABLE IF NOT EXISTS " + table + " ("),
                1, table,
            )

    def test_steam_id_constraint_and_uniqueness(self):
        self.assertIn("steam_id64 TEXT NOT NULL UNIQUE", profiles.SCHEMA)
        self.assertIn("CHECK (steam_id64 ~ '^7656119[0-9]{10}$')", profiles.SCHEMA)

    def test_no_legacy_destructive_migrations(self):
        upper = profiles.SCHEMA.upper()
        for forbidden in ("DROP TABLE", "TRUNCATE", "DELETE FROM DRIVER_LINKS",
                          "UPDATE DRIVER_PROGRESS", "ALTER TABLE DRIVER_LINKS"):
            self.assertNotIn(forbidden, upper)

    def test_module_compiles(self):
        with open(profiles.__file__, encoding="utf-8") as file:
            ast.parse(file.read())


if __name__ == "__main__":
    unittest.main()
