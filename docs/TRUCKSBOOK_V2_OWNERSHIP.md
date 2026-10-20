> Ordinary registration does **not** require staff-witnessed review. Steam
> authentication and provider account linking are separate registration steps.
> The optional review below concerns stronger ownership evidence only; it must
> never block onboarding, imply job ownership or award mileage. See
> `REGISTRATION_WORKFLOW_V2.md` for the current registration contract.

# TrucksBook V2 ownership — authenticated profile review

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
- The obsolete witnessed-Steam function still rejects before database access.
  A distinct independent-staff profile review is available in isolated staging
  only; it cannot authorise mileage or cross-provider reconciliation.
- The old `trucksbook_steam_authenticated_session` evidence label cannot
  authorise V2 mileage or resolve cross-provider review cases.
- Existing profiles, links, mileage, ranks and achievements are not deleted,
  reset or rewritten. Production's existing TrucksBook processor is unchanged.
- Trucky signed-event verification and A&T Steam OpenID remain independent.

## Authenticated provider observation — 20 October 2026

The owner supplied an authenticated email-login session. Read-only inspection
of the official TrucksBook account, profile, settings and delivery pages found:

- The signed-in account menu links to a stable `/profile/<account-id>` and
  `/logbook/<account-id>`; the profile links to A&T company 229356.
- The profile has an official Steam account link. In `/settings`, its matching
  Steam profile URL is a **read-only input**; the adjacent editable control
  changes visibility only. No editable Steam identity control was observed.
- Delivery 68093174 was opened from that account's personal logbook. Its owner
  is a link to the same numeric profile, not merely an equal display name.
- The job page reports ETS2, Paris to Lille, Pet Food, Scania S, 176 accepted
  miles, Real classification and 719 seconds. It shows server time 22:29 and
  local time 21:29 on 18 October 2026. The account timezone is Europe/London,
  so the local display corresponds to 20:29 UTC (minute precision).

Private account and Steam identifiers are intentionally omitted here. This is
an authenticated browser observation, not a discovered OAuth/API capability or
cryptographically signed account attestation. A read-only HTML field alone is
not proof that the relationship is permanently immutable.

## Concrete staff review procedure (no Premium required)

1. The applicant verifies Steam with A&T Steam OpenID and submits their numeric
   TrucksBook account ID in their private registration channel.
2. Authorised staff witness the applicant's authenticated email-login account
   session without receiving passwords, verification codes or cookies. Confirm
   the account menu, stable profile ID and active A&T company membership.
3. Inspect the provider's Steam link and matching read-only Steam URL in settings.
   If missing, editable, hidden from the witness or inconsistent, keep pending.
4. Compare that provider-observed SteamID with the applicant's A&T verified Steam
   link. The owner-only staging diagnostic now supports
   `!stagingidentityaudit <profile ID> <SHA-256 of observed SteamID64>`.
   Hash the ASCII decimal ID, with no newline. It reports flags only and performs
   SELECTs only. A matching digest is comparison evidence, never authentication
   or approval. It must accompany the actual authenticated browser observation.
5. Independent staff must review the observation, membership, matching verified
   Steam identity and absence of conflicting account ownership. Staff cannot
   approve their own claim. Preserve a redacted evidence record, reviewer and
   observation time. Never reuse the retired Steam-login evidence label.

**Current software boundary:** independent staff can record this new review in
isolated staging using the existing private applicant-channel command:
`!approveprovider trucksbook <observed SteamID64> <observed TrucksBook ID> <evidence SHA-256> witnessed-profile-steam-member`.
The explicit marker attests that staff actually witnessed the authenticated
account, read-only mapping and active membership; it is not an automated
provider API assertion. Existing staff-role and no-self-review checks apply.
The transaction verifies the stored Steam link and claimed account, rejects
conflicting ownership and saves the reviewer and evidence reference under
`trucksbook_staff_reviewed_profile_steam_mapping`. It does not activate the
profile, registration session, mileage, roles or achievements. Unique provider
links and transaction rollback prevent partial account reassignment. An additive
JSONB history column preserves the preceding review provenance; exact retries
do not overwrite or append a second review. Old
self-approved claims can be independently re-reviewed; their provenance is
never upgraded automatically. The old marker `confirm-member` remains invalid.

This **new staff evidence is still rejected by the mileage engine and production
ledger adapter**. Separate reviewed job evidence, duplicate reconciliation,
feed completeness and release approval are required before any credits.

Live read-only staging comparison on 20 October confirmed matching observed
Steam, claim and existing provider link, with no conflicting owner. The old
stored claim had no ownership evidence kind and was self-reviewed. It was left
unchanged; a different authorised staff member must witness and record a fresh
review. No owner action can substitute for the independent reviewer requirement.

## Reconciliation boundary

The authentic TrucksBook delivery resolves the previous missing account-linked
job record, but has no observed shared journey identifier with Trucky 27848310.
The matching route, cargo, time and displayed distance support a candidate only.
The Paris–Lille pair remains pending; neither automatic merging nor 352-mile
credit is permitted. A staff identity review does not prove the jobs are the
same journey or establish provider feed completeness.

## Backup verification (separate acceptance)

Both genuine production backup objects were retrieved through authenticated
read-only S3 GET requests. Their recomputed SHA-256 hashes match the uploaded
copies. Both were restored into local embedded PostgreSQL 18.6; 28 SQL migration,
preservation, replay and rollback checks passed. This does not rehearse Railway
service rollback or multiple native PostgreSQL worker processes. Production and
the preserved Railway restore were not changed.
