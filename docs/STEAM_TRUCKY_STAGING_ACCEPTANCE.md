# A&T V2 — Steam and Trucky staging acceptance

Production is intentionally unchanged. Do not deploy the development branch to
the live A&T Command Bot until all acceptance checks pass.

## Trucky identity field inspection
1. Configure the staging Trucky API token; do not print it or commit it.
2. With a management role, run `!truckyidentitycheck 209123` in a restricted channel.
3. Record returned **field names and types only**. This command does not return
   Steam IDs or approve accounts.
4. Confirm the provider API contract and whether a Steam field represents the
   member's authenticated Steam account. If absent or ambiguous, do not auto-approve.

## HTTP routes in the bot service
After database setup, additive schema installation and TrucksBook backfill
succeed, the bot starts an aiohttp listener on Railway's `PORT`. It exposes
`GET /healthz`, `GET /auth/steam/callback` and `POST /trucky/webhook`. The Steam
callback uses the bot's PostgreSQL pool. A valid Trucky HMAC is stored only as
its SHA-256 digest, event name and candidate job ID in
`unified_trucky_webhook_receipts`; no driver identity or distance is stored,
and no mileage is awarded. Missing or invalid HMAC is rejected.

## Steam login
1. Use an isolated PostgreSQL staging database with additive schema installed.
2. Configure `AT_STEAM_CALLBACK_URL` and `AT_STEAM_REALM` with the HTTPS public
   origin and `/auth/steam/callback`; point the Steam OpenID return URL to the
   bot service's generated Railway domain.
3. Driver uses `/register`, then `!linksteam`, signs in via Steam.
4. `!steamstatus` must show a verified SteamID64 only after Steam's OpenID
   `check_authentication` validates the assertion.
5. Reject expired states, repeated state/nonce, a Steam account claimed by a
   second Discord user, foreign callback origins, and invalid assertions.

## Steam-to-Trucky verification
- The authenticated company-member record has a Trucky member ID and profile
  fields but no Steam identity. Company membership alone is not ownership proof.
- Trucky's documented signed API `job_completed` event includes a driver user
  reference with `steam_profile.steam_id`. The receiver accepts a link only
  when the HMAC is valid, the driver/job IDs and timezone-aware completion time
  are present and unambiguous, that SteamID64 matches the driver's Steam
  OpenID link, the Trucky account remains on A&T's authenticated active roster,
  and the matching account claim is still pending within its 30-minute window.
- Claims cannot be approved by moderator acknowledgement. Trucky account and
  Steam identities remain unique across driver profiles. Replayed events are
  idempotent and can retry a post-link Discord role sync.
- The signed event is used only for identity proof. No mileage is awarded by
  this route; job ingestion and cross-provider reconciliation remain separate
  fail-closed gates.
- TrucksBook retains its existing registration and mileage path.

## Current limitations
The successful staging job probe confirmed the company roster has no Steam
field. The Trucky webhook identity field names are documented, but a live
signed job event has not yet been observed in isolated staging. Staging still
needs a public receiver endpoint, isolated PostgreSQL, Steam callback settings,
and a staging-scoped Trucky webhook secret before the entire browser-to-bot
flow can be accepted.

## Atomic automatic approval gate (development)
The receiver now calls `auto_approve_trucky_if_steam_matches` only after HMAC,
payload, active membership and Steam identity checks. The transaction verifies
the job receipt, claim expiry, Steam ownership, and provider/account uniqueness
before activating the unified profile. It does not ingest mileage.

Automated acceptance tests cover signed identity parsing, invalid signatures,
missing/mismatched Steam IDs, active-roster gating, ownership mismatch, claim
expiry, account uniqueness, and idempotent replay. Staging acceptance still
requires one genuine signed Trucky event and a real Steam OpenID login against
an isolated PostgreSQL database.

Never pass `trusted_provider_source=True` based on a public profile, driver
input, or merely the presence of a field called steam_id.

## Release delta
The callback and bot share the same Railway process when `PORT` is available.
No production deployment or data change is authorized by these staging checks.
Trucky mileage remains disabled until the live signed event schema and
cross-provider duplicate reconciliation pass staging acceptance.
