# A&T V2 — Standard-driver acceptance test gate

**Do not ask the tester to leave the live A&T Discord server yet.**

## Release gates
- [ ] GitHub Actions V2 workflow is green, including disposable PostgreSQL integration tests.
- [ ] Test callback service is reachable through a dedicated HTTPS staging hostname.
- [ ] Staging bot has a separate Discord application token and an isolated test guild.
- [ ] Staging database is independent of production; no production credentials or migration.
- [ ] Trucky API returns a documented provider-controlled SteamID64 that matches
      a Steam OpenID-authenticated account, OR automatic Trucky approval stays disabled.
- [ ] Driver role grant/revoke is implemented and validated for Trucky-only drivers.
- [ ] Existing TrucksBook registrations, mileage, ranks, and achievements are regression-tested.
- [ ] Owner approves promotion after staging evidence is reviewed.

## Standard-driver rehearsal (isolated test guild)
1. Tester joins isolated guild as a standard member (no owner/manager role).
2. Tester invokes /register; private registration channel is created once.
3. Tester links Steam and checks !steamstatus.
4. Tester submits their Trucky account; incorrect and duplicate IDs are rejected.
5. With verified provider-side Steam ID and membership, the registration
   should activate and apply appropriate Discord driver roles.
6. Tester leaves and rejoins the isolated guild; check that identity remains
   unique and no mileage is lost or duplicated.
7. Capture screenshots and logs with Steam OpenID tokens and secrets redacted.

**Current status:** This is a test plan, not an approval to perform the live
leave/rejoin test. The Trucky Steam ID source and Discord role completion remain
open blockers. A&T Sentinel and production A&T Command are out of scope.
