# TrucksBook V2 ownership — pending provider-supported evidence

## Corrected provider contract

Normal TrucksBook login uses email/password. TrucksBook's initial registration
uses Steam to check game ownership; it is not a repeatable Steam login for an
existing TrucksBook account. A&T must not instruct existing drivers to sign out
and then sign in to TrucksBook through Steam.

Official sources checked on 19 October 2026:
- https://trucksbook.eu/users/sign_in — normal email/password login.
- https://trucksbook.eu/registration — initial Steam registration/game check.
- https://trucksbook.eu/wiki — registration followed by email activation/login.

A&T Steam OpenID verification remains separate. Knowing a SteamID64, supplying
a TrucksBook profile ID, matching names, company membership, a CSV, or a Discord
webhook does not establish ownership of the provider account. A screenshot or
SHA-256 digest alone does not authenticate the underlying assertion.

## Current safe behaviour

- `!registertrucksbook <numeric profile ID>` retains a pending claim only.
- `!approveprovider trucksbook ...` explains the ownership blocker and performs
  no approval. The obsolete witnessed-Steam function also rejects before any
  database access, including stale callers with apparently matching IDs.
- The old `trucksbook_steam_authenticated_session` evidence label cannot
  authorise V2 mileage or resolve cross-provider review cases.
- Existing profiles, links, mileage, ranks and achievements are not deleted,
  reset or rewritten. Production's existing TrucksBook processor is unchanged.
- Trucky signed-event verification and A&T Steam OpenID remain independent.

## Evidence still required

An observed, provider-supported method must prove control of the stable
TrucksBook account to the same Discord applicant who separately verified Steam.
If the provider exposes its authenticated Steam relationship, compare that
provider-owned field with A&T's verified SteamID64. Do not invent such a field
or infer it from the original registration requirement.

The next investigation is the driver's authenticated TrucksBook account and
company administration pages, or a reply from official TrucksBook support.
Inspect whether they expose a provider-maintained stable account/Steam mapping
or an authenticated account action suitable for a one-time control challenge.
No action or endpoint is assumed to exist. An owner-assisted alternative must
be verified and tested before enabling approval; observing email login alone
is not the missing Steam relationship proof.

Drivers must not disclose passwords, Steam Guard/email verification codes,
session cookies or recovery codes. A&T staff must not approve their own claims.
No Premium subscription is assumed or required by the pending-claim flow.

## Backup verification (separate acceptance)

Both genuine production backup objects were retrieved through authenticated
read-only S3 GET requests. Their recomputed SHA-256 hashes match the uploaded
copies. Both were restored into local embedded PostgreSQL 18.6; 28 SQL migration,
preservation, replay and rollback checks passed. This does not rehearse Railway
service rollback or multiple native PostgreSQL worker processes. Production and
the preserved Railway restore were not changed.
