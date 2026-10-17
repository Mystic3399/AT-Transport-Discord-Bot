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

## Automated registration gate
- Steam ownership verification **and** exact SteamID64 equality against a
  trustworthy provider-owned account field **and** current A&T membership
  must all pass before automated provider activation.
- Neither the public Trucky ID nor membership roster alone proves ownership.
- TrucksBook identity extraction is not yet established; keep legacy flow.
- Driver progression/mileage for Trucky-only drivers is not yet complete.
- Preserve all existing mileage and ranks. Do not deploy or migrate live data.

## Current limitations
The standalone callback and Discord bot are not deployed together; provider
Steam field provenance and end-to-end tests are not verified. The old
`!confirmprovider` management beta path must not be enabled as a substitute
for Steam-backed automated verification.

## Atomic automatic approval gate (development)
The `auto_approve_trucky_if_steam_matches` database function is implemented,
but has no production caller. It must only be called with trusted provider Steam
data, independently verified current A&T membership, and a matching pending
Trucky claim. It does not assign Discord roles or ingest mileage.

Acceptance tests still required:
- Correct Steam identity + matching Trucky ID + current membership: approve.
- Incorrect Steam identity: no provider link or active profile.
- Reused Trucky ID owned by a different Discord user: reject.
- Missing trusted Steam field: reject and flag exception.
- Concurrent claims: one winner only; no partial activation.
- Existing TrucksBook mileage and progression: unchanged.

Never pass `trusted_provider_source=True` based on a public profile, driver
input, or merely the presence of a field called steam_id.

## Release delta
The callback and bot now share the same Railway process when `PORT` is
available. No development Railway domain or callback configuration has been
applied. Signed Trucky receipts are durably quarantined for independent API
review, but are not yet joined to an independently fetched completed-job
record. Therefore the receiver is not a mileage ingestion or award path.
