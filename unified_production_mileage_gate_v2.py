"""Fail-closed production activation boundary; intentionally has no writer.

The V2 staging worker only targets disposable test databases. This module is
the separate, read-only preflight predicate for a future production adapter;
no current bot path calls it and no production mileage writer is provided.
"""
import hashlib
import json
import os
import re


PRODUCTION_ENVIRONMENT_ID = "72d029c5-98b4-4440-8492-8db12620d9da"
PRODUCTION_SERVICE_ID = "73f8817c-36be-423a-b68b-b457232cfcfd"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


async def read_only_database_fingerprint(connection):
    """Return a stable opaque fingerprint using a read-only PostgreSQL query."""
    async with connection.transaction(readonly=True):
        row = await connection.fetchrow("""
            SELECT current_database() AS database_name,
                   oid::text AS database_oid
            FROM pg_database
            WHERE datname = current_database()
        """)
    if not row or not row["database_name"] or not row["database_oid"]:
        raise RuntimeError("Database identity could not be verified")
    value = json.dumps(
        [str(row["database_name"]), str(row["database_oid"])],
        separators=(",", ":"),
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def production_activation_blockers(config=None, runtime=None):
    """Return non-sensitive blocker codes; never activate based on staging flags.

    `config` is deployment-specific Railway configuration. `runtime` contains
    independently observed deployment/database/source/preflight facts supplied
    by a future adapter. This predicate alone does not authorize database writes.
    """
    env = os.environ if config is None else config
    observed = {} if runtime is None else runtime
    blockers = []

    if env.get("AT_V2_ISOLATED_STAGING") == "YES":
        blockers.append("isolated_staging_runtime")
    if env.get("AT_V2_TEST_DATABASE_DISPOSABLE") == "YES":
        blockers.append("disposable_database_runtime")
    if env.get("AT_V2_MILEAGE_ENGINE_TEST_ENABLED") == "YES":
        blockers.append("test_override_present")
    if env.get("AT_V2_PRODUCTION_MILEAGE_ENABLED") != "YES":
        blockers.append("production_activation_disabled")
    if not env.get("AT_V2_PRODUCTION_OWNER_APPROVAL_REF"):
        blockers.append("owner_approval_missing")

    expected = {
        "AT_V2_RUNTIME_RAILWAY_ENVIRONMENT_ID": PRODUCTION_ENVIRONMENT_ID,
        "AT_V2_RUNTIME_RAILWAY_SERVICE_ID": PRODUCTION_SERVICE_ID,
    }
    for key, value in expected.items():
        if env.get(key) != value or observed.get(key.removeprefix("AT_V2_RUNTIME_")) != value:
            blockers.append("production_deployment_identity_mismatch")
            break

    approved_commit = env.get("AT_V2_APPROVED_RELEASE_COMMIT")
    if (not approved_commit or observed.get("commit_sha") != approved_commit
            or not re.fullmatch(r"[0-9a-f]{40}", approved_commit)):
        blockers.append("approved_release_commit_mismatch")
    deployment_id = env.get("AT_V2_APPROVED_DEPLOYMENT_ID")
    if (not deployment_id or observed.get("deployment_id") != deployment_id
            or observed.get("deployment_id") != env.get("AT_V2_RUNTIME_DEPLOYMENT_ID")):
        blockers.append("approved_deployment_mismatch")

    expected_db = env.get("AT_V2_EXPECTED_DATABASE_FINGERPRINT") or ""
    if (not SHA256_RE.fullmatch(expected_db)
            or observed.get("database_fingerprint") != expected_db):
        blockers.append("production_database_identity_unverified")
    if observed.get("source_authentication_verified") is not True:
        blockers.append("provider_source_authentication_unverified")
    if observed.get("preflight_acceptance_passed") is not True:
        blockers.append("preflight_acceptance_missing")
    preflight_digest = env.get("AT_V2_APPROVED_PREFLIGHT_SHA256") or ""
    if (not SHA256_RE.fullmatch(preflight_digest)
            or observed.get("preflight_sha256") != preflight_digest):
        blockers.append("preflight_digest_mismatch")
    if observed.get("historical_overlap_audit_passed") is not True:
        blockers.append("historical_overlap_audit_missing")
    if observed.get("backup_restore_rollback_rehearsal_passed") is not True:
        blockers.append("backup_restore_rollback_rehearsal_missing")
    return tuple(dict.fromkeys(blockers))


def production_activation_allowed(config=None, runtime=None):
    """True only when every production-only gate is independently satisfied."""
    return not production_activation_blockers(config, runtime)
