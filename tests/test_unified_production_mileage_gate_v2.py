import unittest

from unified_production_mileage_gate_v2 import (
    PRODUCTION_ENVIRONMENT_ID,
    PRODUCTION_SERVICE_ID,
    production_activation_allowed,
    production_activation_blockers,
    read_only_database_fingerprint,
)


class ProductionMileageGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fingerprint = "a" * 64
        self.preflight = "b" * 64
        self.config = {
            "AT_V2_PRODUCTION_MILEAGE_ENABLED": "YES",
            "AT_V2_PRODUCTION_OWNER_APPROVAL_REF": "owner-approval-record",
            "AT_V2_APPROVED_DEPLOYMENT_ID": "deploy-production-01",
            "AT_V2_APPROVED_RELEASE_COMMIT": "c" * 40,
            "AT_V2_EXPECTED_DATABASE_FINGERPRINT": self.fingerprint,
            "AT_V2_APPROVED_PREFLIGHT_SHA256": self.preflight,
        }
        self.runtime = {
            "RAILWAY_ENVIRONMENT_ID": PRODUCTION_ENVIRONMENT_ID,
            "RAILWAY_SERVICE_ID": PRODUCTION_SERVICE_ID,
            "RAILWAY_DEPLOYMENT_ID": "deploy-production-01",
            "RAILWAY_GIT_COMMIT_SHA": "c" * 40,
            "database_fingerprint": self.fingerprint,
            "source_authentication_verified": True,
            "preflight_acceptance_passed": True,
            "preflight_sha256": self.preflight,
            "legacy_writer_drained": True,
            "historical_overlap_audit_passed": True,
            "backup_restore_rollback_rehearsal_passed": True,
        }

    def test_default_is_disabled(self):
        blockers = production_activation_blockers({}, {})
        self.assertIn("production_activation_disabled", blockers)
        self.assertFalse(production_activation_allowed({}, {}))

    def test_complete_production_specific_acceptance_can_pass_predicate(self):
        self.assertEqual(production_activation_blockers(self.config, self.runtime), ())
        self.assertTrue(production_activation_allowed(self.config, self.runtime))

    def test_staging_and_disposable_overrides_can_never_enable_production(self):
        for key in ("AT_V2_ISOLATED_STAGING", "AT_V2_TEST_DATABASE_DISPOSABLE",
                    "AT_V2_MILEAGE_ENGINE_TEST_ENABLED"):
            with self.subTest(key=key):
                config = {**self.config, key: "YES"}
                self.assertFalse(production_activation_allowed(config, self.runtime))

    def test_wrong_service_database_or_commit_fails_closed(self):
        for runtime_key, value, expected_reason in (
            ("RAILWAY_SERVICE_ID", "staging-service", "production_deployment_identity_mismatch"),
            ("database_fingerprint", "d" * 64, "production_database_identity_unverified"),
            ("RAILWAY_GIT_COMMIT_SHA", "e" * 40, "approved_release_commit_mismatch"),
            ("source_authentication_verified", False, "provider_source_authentication_unverified"),
            ("preflight_acceptance_passed", False, "preflight_acceptance_missing"),
            ("legacy_writer_drained", False, "legacy_writer_not_drained"),
            ("historical_overlap_audit_passed", False, "historical_overlap_audit_missing"),
            ("backup_restore_rollback_rehearsal_passed", False,
             "backup_restore_rollback_rehearsal_missing"),
        ):
            with self.subTest(runtime_key=runtime_key):
                runtime = {**self.runtime, runtime_key: value}
                self.assertIn(expected_reason,
                              production_activation_blockers(self.config, runtime))

    async def test_database_fingerprint_query_runs_read_only(self):
        class Transaction:
            def __init__(self):
                self.readonly = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        class Connection:
            def __init__(self):
                self.tx = Transaction()
                self.sql = ""

            def transaction(self, *, readonly=False):
                self.tx.readonly = readonly
                return self.tx

            async def fetchrow(self, sql):
                self.sql = sql
                return {"database_name": "isolated_restore", "database_oid": "16384"}

        conn = Connection()
        fingerprint = await read_only_database_fingerprint(conn)
        self.assertTrue(conn.tx.readonly)
        self.assertIn("pg_database", conn.sql)
        self.assertEqual(len(fingerprint), 64)


if __name__ == "__main__":
    unittest.main()

