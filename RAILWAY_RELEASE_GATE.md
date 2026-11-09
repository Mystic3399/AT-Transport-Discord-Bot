# A&T Command — Railway production release gate

Target service: `AT-Transport-Discord-Bot` in project `00313eef-3512-45ec-a4fa-fdcb815235a2`.
Production environment: `72d029c5-98b4-4440-8492-8db12620d9da`.
Current live source: `fix/dual-provider-mileage-desktop`, pinned commit `ae24308682140b6dcb533678e1b350288d6c91d8`.
Candidate source: `fix/production-mileage-audit-automation`.

## Release gate — all required

1. Both GitHub Actions workflows must pass on the **final** candidate commit.
2. Run the disposable PostgreSQL integration suite against a local `/v2_test` database with `AT_V2_TEST_DATABASE_DISPOSABLE=YES`. GitHub's standard CI skips database tests.
3. Inspect read-only historical coverage per driver. The 219 held jobs must remain held unless supported by verified route, ownership and balance evidence. No blanket approval.
4. Snapshot production database, confirm restore capability and compare driver totals and ledger counts before deployment.
5. Verify every driver-profile, leaderboard, Hall of Fame and milestone reader uses the intended unified totals; reconcile a known Real/Race example.
6. Obtain explicit owner approval for the **exact final commit**. Update `AT_V2_REGISTRATION_APPROVED_COMMIT` only in the controlled release, and verify the actual database fingerprint. Do not print approval tokens or secrets.
7. Execute `python deployment_preflight_v2.py` inside the target Railway environment; every check must pass. The preflight checks configuration only: independently verify the actual database fingerprint with the production read-only gate.
8. Stage Railway source and variable changes together, with a documented rollback to the previous pinned source commit and prior approval value.
9. Deploy one replica; watch for activation, errors, new credit decisions, and idempotent replays. Compare mileage before and after; halt and roll back on unexpected credits or registration/role changes.

**Do not merge or deploy solely because unit CI passes.** CI deliberately does not connect to production or execute the disposable database integration tests.
