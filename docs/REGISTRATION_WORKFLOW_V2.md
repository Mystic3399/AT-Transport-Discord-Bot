# Ordinary driver registration

This corrects the former conflation of onboarding with mileage ownership review.
No staff witness or independent reviewer is required for ordinary registration.

1. `/register` in the configured onboarding channel creates a private verification
   channel and durable registration embed, or refreshes the existing channel.
2. `!linksteam` issues the existing short-lived Steam OpenID link. A successful
   authenticated callback persists the unique Steam identity and automatically
   edits the same embed to show the confirmed Steam account as Verified.
3. `!trucksbook <numeric profile ID>` (also `!registertrucksbook`) uses the existing
   TrucksBook profile and A&T membership lookup. `!registertrucky <numeric account
   ID>` uses the authenticated existing Trucky roster lookup.
4. A successful provider lookup records a Linked account, not authenticated
   ownership. Existing links and approval evidence are preserved. Duplicate
   claims and conflicting account replacements are rejected transactionally.
5. Steam Verified plus at least one Linked or Verified tracking account completes
   the unified registration session automatically. Either provider or both may be
   used; the other provider may be linked later without reopening onboarding.
6. `/myregistration`, `/register` and `!steamstatus` can refresh status if Discord
   was unavailable during the callback. OpenID success remains committed and must
   not be replayed when an embed edit fails.

The four provider statuses mean:

- **Not Linked**: no account claim or link.
- **Pending**: claim recorded, account not linked yet.
- **Linked**: the chosen provider account is recorded; no independent ownership
  assertion follows from its name, ID, public profile or membership alone.
- **Verified**: matching stored ownership evidence and verified provider link.

Registration writes only unified identity/registration tables and the embed
message reference. It never writes legacy mileage, processed jobs, progression,
achievements, Company Mods access or Discord roles. Completing registration does
not make any delivery eligible for mileage. Existing workers still require job
ownership, authenticated completion, classification and reconciliation evidence.
The optional stronger staff review is not part of this normal path.

## Isolated staging activation

The revised registration path requires all three settings:
`AT_V2_ISOLATED_STAGING=YES`, `AT_V2_STAGING_REGISTRATION_ENABLED=YES` and the exact
Railway staging environment identity. This opt-in permits explicit registration
and Steam callback writes, not webhook mileage/role writes. Without the opt-in,
the previous read-only staging callback protection remains in place. Production
is neither deployed nor activated by these settings.

## Demonstration and evidence limits

The disposable PostgreSQL end-to-end regression starts a new registration,
exercises the actual HTTP callback with a mocked Steam provider verification,
verifies its notification hook, links TrucksBook and Trucky, confirms onboarding,
retries commands/callbacks, rejects conflicting replacements and reopens the
registration without losing links. Provider ownership flags remain false and
no job credit is created. A separate Discord embed test confirms the existing
message is edited with separate Steam/TrucksBook/Trucky fields. These are
integration simulations, not evidence of a fresh human Steam sign-in or live
provider ownership. Live acceptance must be reported separately.
