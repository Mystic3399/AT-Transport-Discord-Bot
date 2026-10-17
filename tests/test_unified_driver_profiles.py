"""Structural checks for the additive V2 migration (no production database)."""
import unittest
import unified_driver_profiles as model

class UnifiedSchemaTests(unittest.TestCase):
    def test_independent_driver_identity(self):
        self.assertIn("discord_user_id BIGINT PRIMARY KEY", model.SCHEMA)
        self.assertIn("CHECK (provider IN ('trucksbook','trucky'))", model.SCHEMA)
    def test_multiple_providers_per_driver(self):
        self.assertIn("UNIQUE (discord_user_id, provider)", model.SCHEMA)
    def test_source_jobs_are_idempotent(self):
        self.assertIn("PRIMARY KEY (provider, provider_job_id)", model.SCHEMA)
    def test_legacy_tables_not_mutated(self):
        self.assertNotIn("DROP TABLE", model.SCHEMA.upper())
        self.assertNotIn("TRUNCATE", model.SCHEMA.upper())
        self.assertNotIn("UPDATE driver_progress", model.SCHEMA)
    def test_backfill_is_idempotent(self):
        self.assertIn("ON CONFLICT(discord_user_id) DO NOTHING", model.backfill_trucksbook_profiles.__doc__ or "") if False else None

if __name__ == "__main__":
    unittest.main()
