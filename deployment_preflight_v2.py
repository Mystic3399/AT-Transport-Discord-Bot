"""Non-mutating deployment preflight for A&T linked mileage.

Prints only booleans and commit identifiers, never secrets or database URLs.
Use in Railway's target environment before enabling the new commit.
"""
import os
import re
import sys

from trucky_linked_allocation_v2 import activation_allowed

EXPECTED_PROJECT = "00313eef-3512-45ec-a4fa-fdcb815235a2"
EXPECTED_ENVIRONMENT = "72d029c5-98b4-4440-8492-8db12620d9da"
EXPECTED_SERVICE = "73f8817c-36be-423a-b68b-b457232cfcfd"


def preflight(config):
    checks = {
        "production_target": (
            config.get("RAILWAY_PROJECT_ID") == EXPECTED_PROJECT
            and config.get("RAILWAY_ENVIRONMENT_ID") == EXPECTED_ENVIRONMENT
            and config.get("RAILWAY_SERVICE_ID") == EXPECTED_SERVICE
        ),
        "database_fingerprint_configured": bool(
            re.fullmatch(r"[0-9a-f]{64}", config.get("AT_V2_LINKED_MILEAGE_DATABASE_SHA256", ""))
        ),
        "owner_approval_present": bool(
            config.get("AT_V2_LINKED_MILEAGE_OWNER_APPROVAL_REF", "").strip()
        ),
        "new_commit_explicitly_approved": bool(
            re.fullmatch(r"[0-9a-f]{40}", config.get("RAILWAY_GIT_COMMIT_SHA", ""))
            and config.get("RAILWAY_GIT_COMMIT_SHA")
            == config.get("AT_V2_REGISTRATION_APPROVED_COMMIT")
        ),
        "linked_allocation_activated": activation_allowed(config),
        "automatic_reconciliation_enabled": (
            config.get("AT_V2_AUTOMATIC_LINKED_RECONCILIATION") == "YES"
        ),
    }
    return checks


def main():
    checks = preflight(os.environ)
    for name, passed in checks.items():
        print(f"{name}: {'PASS' if passed else 'BLOCKED'}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
