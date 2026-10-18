# A&T Command Bot V2 migration safeguards

## Current production behavior

The production bot receives TrucksBook Discord webhook messages in `bot.py`'s
`on_message` handler. The legacy path resolves the TrucksBook name through the
existing driver links, then calls `record_real_job`. That function inserts into
`processed_jobs` and increments `driver_progress` inside one PostgreSQL
transaction. `processed_jobs.job_id` is the legacy provider-job idempotency
key. Outside staging, `trucksbook_webhook_auth_v2` intentionally preserves
the legacy behavior and does not enforce the configured staging webhook ID;
driver resolution still depends on the legacy name mapping. These are cutover
risks that must be replaced with provider-source and stable-account checks
before V2 can become the production writer. Progress and existing
role/milestone writers remain part of that legacy path.

The development branch has not replaced that production receiver. In isolated
staging, a configured TrucksBook webhook is source-checked and recorded in
`staging_shadow_receipts`; the handler returns before legacy job parsing or
writers. Signed Trucky events are recorded and checked read-only. Neither
staging path awards mileage.

## Data-preservation properties already enforced

- `driver_progress` and `processed_jobs` are created with `IF NOT EXISTS`; no
  migration in this branch resets or truncates them.
- `backfill_trucksbook_profiles` writes only to unified V2 profile/link tables,
  uses `ON CONFLICT DO NOTHING`, and raises on conflicting TrucksBook ownership.
- Staging receipt, review and award tables are separate from legacy mileage
  tables. Staging startup skips the production identity backfill and startup
  role/leaderboard synchronization.
- Legacy same-provider duplicate handling is transactional: a conflicting
  `processed_jobs.job_id` prevents the progress increment.
- V2 provider event writes require a verified provider link to the same
  Discord profile and the exact stable provider account ID. V2 staging award
  rows require a canonical journey key and enforce unique
  `(discord_user_id, canonical_delivery_key)`.
- The cross-provider review ledger records auditable decisions only. It does
  not assign canonical award keys or call a mileage writer.

## Cutover and rollback constraints

The production webhook route must not be switched until TrucksBook stable
account ownership and cross-provider journey reconciliation are authoritative,
and the V2 writer has a production-compatible transactional migration tested
against a restored copy of the production schema. During any future cutover,
exactly one receiver may award each provider's jobs: do not run the legacy and
V2 award writers concurrently for overlapping deliveries.

The reversible cutover must retain `processed_jobs`, `driver_progress`, all
job receipts and V2 audit rows. Rollback disables the V2 writer/route and
restores the prior deployment/receiver configuration; it must not delete V2
receipts or reverse historical totals automatically. Any queued or partially
processed events remain quarantined for reconciliation before re-enabling a
writer. A production backup and isolated restore must be re-verified against
the exact release candidate before cutover.

## Remaining release gates

1. Obtain provider-backed TrucksBook account identity and confirm a job against
   the linked Steam-verified A&T profile.
2. Establish an authoritative shared journey identifier or independent proof
   for cross-provider duplicates; similarity is candidate-only.
3. Test V2 ingestion and rollback using a restored production-schema copy and
   a single-writer cutover rehearsal.
4. Obtain separate owner approval before any production deployment or mileage
   activation.
