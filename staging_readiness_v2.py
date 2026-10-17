"""Fail-closed readiness report for isolated A&T driver registration V2 staging.

Run without secrets. Does not connect to Discord, Steam, Trucky, TrucksBook,
Railway or production databases, and does not activate any feature.
"""
import json
import os
from urllib.parse import urlsplit

REQUIRED = {
    "AT_V2_STAGING_ONLY": "Explicit staging isolation enabled",
    "AT_V2_STAGING_BOT_TOKEN_PRESENT": "Distinct staging Discord bot token provisioned",
    "AT_V2_STAGING_GUILD_CONFIRMED": "Separate staging Discord guild confirmed",
    "AT_V2_STAGING_DB_CONFIRMED": "Separate disposable staging PostgreSQL confirmed",
    "AT_V2_STEAM_CALLBACK_CONFIRMED": "HTTPS Steam callback and proxy validated",
    "AT_V2_TRUCKY_STEAM_PROVENANCE_CONFIRMED": "Trusted provider-side Trucky Steam identity validated",
    "AT_V2_TRUCKSBOOK_FEED_CONFIRMED": "Authenticated TrucksBook job feed mapped and tested",
    "AT_V2_TRUCKY_FEED_CONFIRMED": "Authenticated Trucky job feed mapped and tested",
    "AT_V2_LEDGER_RECONCILIATION_CONFIRMED": "Atomic deduplication and mileage credit transaction tested",
    "AT_V2_CI_GREEN_CONFIRMED": "V2 GitHub CI and database integration tests passed",
    "AT_V2_DRIVER_ROLE_CONFIRMED": "Staging role permissions and role synchronisation tested",
}


def report(environ=None):
    env = os.environ if environ is None else environ
    checks = {key: env.get(key) == "YES" for key in REQUIRED}
    callback = env.get("AT_STEAM_CALLBACK_URL", "")
    parsed = urlsplit(callback)
    checks["AT_V2_STEAM_CALLBACK_CONFIRMED"] &= (
        parsed.scheme == "https" and bool(parsed.hostname)
        and parsed.path == "/auth/steam/callback"
        and not parsed.username and not parsed.password
    )
    # Self-declarations are a checklist only, never a substitute for evidence.
    return {
        "ready_for_isolated_staging_rehearsal": all(checks.values()),
        "checks": [
            {"name": name, "description": REQUIRED[name],
             "status": "self_attested" if ok else "blocked"}
            for name, ok in checks.items()
        ],
        "warning": "Self-attestation is not proof. Review evidence before any live test.",
    }


if __name__ == "__main__":
    print(json.dumps(report(), indent=2))
    raise SystemExit(0 if report()["ready_for_isolated_staging_rehearsal"] else 2)
