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

## Steam login
1. Use an isolated PostgreSQL staging database with additive schema installed.
2. Configure HTTPS callback URL and realm; route the HTTPS reverse proxy to the
   staging callback service at `/auth/steam/callback`.
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
