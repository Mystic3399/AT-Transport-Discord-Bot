# A&T Command — Railway production release gate

Target service: `AT-Transport-Discord-Bot` in project `00313eef-3512-45ec-a4fa-fdcb815235a2`.
Production environment: `72d029c5-98b4-4440-8492-8db12620d9da`.
Current live source: `fix/dual-provider-mileage-desktop`, pinned commit `ae24308682140b6dcb533678e1b350288d6c91d8`.
Candidate source: `fix/production-mileage-audit-automation`.

## Release gate — all required

1. All three GitHub Actions workflows (mileage regression, Unified Driver V2 safety, disposable PostgreSQL integration) must pass on the **final** candidate commit.
2. Confirm the disposable PostgreSQL integration workflow actually ran (not skipped) against its ephemeral `/v2_test` service with `AT_V2_TEST_DATABASE_DISPOSABLE=YES`. Run additional integration cases locally if production-specific behaviours are not covered.
3. Inspect read-only historical coverage per driver. Railway runtime logs sampled on 2026-11-09 at 20:47 UTC reported 219 `historical_route_coverage_pending`, 6 `invalid_or_conflicting_pending`, 1 `delivery_window_pending` and 165 `replayed`; these are operational observations, not a database audit. Held jobs must remain held unless supported by verified route, ownership and balance evidence. No blanket approval.
4. Snapshot production database, confirm restore capability and compare driver totals and ledger counts before deployment.
5. Verify every driver-profile, leaderboard, Hall of Fame and milestone reader uses the intended unified totals; reconcile a known Real/Race example. Run `driver_statistics_consistency_audit_v2.inspect_driver_statistics(conn)` against a reviewed read-only production connection after confirming all required tables exist; inspect each reported mismatch and do not auto-correct balances or permanent records.
6. Obtain explicit owner approval for the **exact final commit**. Update `AT_V2_REGISTRATION_APPROVED_COMMIT` only in the controlled release, and verify the actual database fingerprint. Do not print approval tokens or secrets.
7. Execute `python deployment_preflight_v2.py` inside the target Railway environment; every check must pass. The preflight checks configuration only: independently verify the actual database fingerprint with the production read-only gate.
8. Stage Railway source and variable changes together, with a documented rollback to the previous pinned source commit and prior approval value.
9. Deploy one replica; watch for activation, errors, new credit decisions, and idempotent replays. Compare mileage before and after; halt and roll back on unexpected credits or registration/role changes.

**Do not merge or deploy solely because CI passes.** The disposable PostgreSQL workflow tests isolated allocation behaviour, not production database contents, snapshots, profile readers or the Railway approval fingerprint.


### Read-only driver statistics verification

Use a separately provisioned PostgreSQL account with **SELECT-only** permissions on
the four audit tables. Supply its connection string as
`AT_AUDIT_READONLY_DATABASE_URL` (never the production bot writer credential),
then run `python driver_statistics_consistency_audit_v2.py` from the checked-out
repair branch in a trusted operator environment. The runner sets PostgreSQL
`default_transaction_read_only=on` and a 30-second statement timeout and
prints aggregate counts only; it does not print driver IDs or connection secrets. Exit status 0 means no findings, 1 means at least one affected driver, and 2 means the audit could not run because the schema is missing. A zero exit status is necessary but not sufficient for release approval.

If the result is `schema_missing`, stop and review the schema. If
`affected_drivers` is nonzero, inspect the driver-level report under
controlled access and reconcile evidence before release. This audit does not
approve any mileage credit or replace a verified backup. Do not run it using
the Railway production writer credential.
