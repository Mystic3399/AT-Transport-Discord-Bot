import unittest

from deployment_preflight_v2 import preflight


def approved():
    return {
        "RAILWAY_PROJECT_ID": "00313eef-3512-45ec-a4fa-fdcb815235a2",
        "RAILWAY_ENVIRONMENT_ID": "72d029c5-98b4-4440-8492-8db12620d9da",
        "RAILWAY_SERVICE_ID": "73f8817c-36be-423a-b68b-b457232cfcfd",
        "RAILWAY_GIT_COMMIT_SHA": "a" * 40,
        "AT_V2_REGISTRATION_APPROVED_COMMIT": "a" * 40,
        "AT_V2_LINKED_MILEAGE_DATABASE_SHA256": "b" * 64,
        "AT_V2_LINKED_MILEAGE_OWNER_APPROVAL_REF": "approved",
        "AT_V2_LINKED_TRUCKY_MILEAGE_ENABLED": "YES",
        "AT_V2_PENDING_HISTORY_ENABLED": "YES",
        "AT_V2_TRUCKY_API_INGEST_ENABLED": "YES",
        "AT_V2_AUTOMATIC_LINKED_RECONCILIATION": "YES",
    }


class DeploymentPreflightTests(unittest.TestCase):
    def test_approved_production_configuration(self):
        self.assertTrue(all(preflight(approved()).values()))

    def test_new_commit_without_approval_is_blocked(self):
        config = approved()
        config["RAILWAY_GIT_COMMIT_SHA"] = "c" * 40
        checks = preflight(config)
        self.assertFalse(checks["new_commit_explicitly_approved"])
        self.assertFalse(checks["linked_allocation_activated"])

    def test_staging_or_wrong_database_cannot_activate(self):
        config = approved()
        config["RAILWAY_ENVIRONMENT_ID"] = "staging"
        self.assertFalse(preflight(config)["production_target"])
        self.assertFalse(preflight(config)["linked_allocation_activated"])
        config = approved()
        config["AT_V2_LINKED_MILEAGE_DATABASE_SHA256"] = ""
        self.assertFalse(preflight(config)["linked_allocation_activated"])

    def test_missing_reconciliation_flag_is_reported(self):
        config = approved()
        config["AT_V2_AUTOMATIC_LINKED_RECONCILIATION"] = "NO"
        self.assertFalse(preflight(config)["automatic_reconciliation_enabled"])


if __name__ == "__main__":
    unittest.main()
