# Registration-only release acceptance

This candidate includes the five production hotfix commits through
`a6cc9d60b095fa51b41ca4cd636fe2d549c88deb`. It does not replace the legacy
TrucksBook job processor with the V2 mileage worker.

## Registration activation

Production activation requires separate owner approval. No variables have been
set in production by this change. Keep the following absent until approval:

- `AT_V2_REGISTRATION_ENABLED=YES`
- `AT_V2_REGISTRATION_APPROVED_COMMIT=<full approved release commit SHA>`

The registration gate also requires Railway runtime project, environment and
service IDs to match the existing Command production service, and the approved
SHA to equal `RAILWAY_GIT_COMMIT_SHA`. Staging flags must be absent in production.
Staging uses its existing isolated registration configuration.

This gate enables only the existing registration path: `/register`, Steam
authentication, durable verification embeds, supported account linking,
`/myregistration` and onboarding completion. Provider lookup establishes Linked,
not independently Verified ownership. It does not create legacy mileage links
from unverified claims. New linked drivers therefore do not become eligible for
legacy mileage merely by completing registration.

`!verifications` and the staging slash synchronization helper remain staging-only.
Production management visibility for the new overview is not part of this release.

Cancellation now invalidates outstanding Steam sign-in states in the same
transaction as both registration records, while retaining linked accounts and
historical driver profiles. Steam binding and signed Trucky identity updates
serialize against cancellation and cannot reactivate suspended drivers.
An existing Linked Trucky account can become Verified after valid signed identity
evidence, without replacing its ownership or generating a mileage credit.
The bot's Trucky identity callback refreshes only the verification embed; it does
not call the role writer. `/register` can resume interrupted onboarding after a
successful Steam binding without replaying its consumed OpenID response.

## Required acceptance before a production request

- CI with disposable PostgreSQL passes at the exact candidate commit.
- The candidate runs in staging with unchanged safe-mode protections.
- Preserve live registration and private management command acceptance evidence.
- Confirm production Steam callback URL/realm and private verification category,
  management roles and onboarding channel settings without copying staging IDs.
- Confirm post-incident credential rotation and current recoverable backup.
- Rehearse the candidate against an isolated restored database, including
  preservation of legacy driver links, processed jobs, mileage and achievements.
- Record the production deployment and rollback build; do not automatically
  roll back the database or delete additive identity records.
- Obtain owner approval for the exact candidate and registration-only scope.

V2 mileage, cross-provider automatic credit and expanded Company Mods eligibility
remain outside this release. Existing production mileage and role behaviour must
not be disabled by setting the isolated-staging flag in production.

## Reversibility

Removing `AT_V2_REGISTRATION_ENABLED` disables the new registration path on the
next configuration deployment; identity rows remain intact. Returning to the
recorded production build restores the previous command behaviour. Do not replay
historical jobs or reset totals during either operation. Credential changes and
database restoration require their own verified procedures.
