# A&T Unified Driver Registration V2 — implementation gates

## Driver-facing workflow
- /register creates one private channel for the Discord member.
- Within the channel, the driver may submit a TrucksBook ID, a Trucky ID, or both.
- Both claims stay pending until ownership AND A&T company membership are independently confirmed by authorised management.
- One approved provider activates the unified driver profile.
- Drivers may add the second provider later without resetting their profile.
- /myregistration displays both provider statuses privately.

## Legacy compatibility
- Existing TrucksBook-linked drivers retain driver_links, driver_progress, processed_jobs, ranks and achievements.
- Additive backfill creates unified_driver_profiles and unified_provider_links without deleting or overwriting legacy records.
- Legacy !trucksbook verification and its approvals remain in production until the new workflow is validated.
- Existing Trucky beta has a driver_links foreign key and cannot support Trucky-only users; migrate to the new claim and link tables rather than removing that foreign key in place.

## Non-negotiable mileage safeguards
- Do not import Trucky jobs until API authentication, event schema, pagination, stable IDs, and replay handling are verified.
- Record raw provider events idempotently. Cross-provider matching is separate, reviewed, and reversible.
- Do not sum both providers' mileage until duplicate detection is validated against actual overlapping jobs.
- Keep Real and Race Miles distinct, preserve existing TrucksBook real-mile totals, and avoid changing role milestones during migration.
- Make database backup and restore verification prerequisites for production schema changes.

## Release gates
1. Validate schema and migration in disposable PostgreSQL with duplicate IDs, concurrent claims, repeated backfill and rollback tests.
2. Implement the private-channel commands and approval controls on this branch.
3. Test new TrucksBook-only, new Trucky-only, both, existing driver, denied ownership, stale channel, missing permissions, and cancellation.
4. Verify Trucky API access without exposing its token; test ingestion with captured redacted events.
5. Owner closed beta on a staging bot and database.
6. Review results before any production activation.
