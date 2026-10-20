# Management verification overview

`!verifications` or `!verifications 2` shows up to ten registration records per
page. Each record contains the Discord member and separate Steam, TrucksBook,
Trucky and registration statuses. Linked never means authenticated ownership.

Access requires the existing configured verification-management role, the
configured staging guild and the isolated V2 registration setting. Set
`AT_V2_VERIFICATIONS_ADMIN_CHANNEL_IDS` to a comma-separated list of existing
private management channel IDs on the staging bot service only. Missing or
malformed configuration fails closed before any member records are read.
The channel must deny @everyone visibility and must not explicitly grant
visibility to non-management members or roles. Administrators retain their
existing Discord access. Threads and applicant verification channels are not
accepted as substitutes for configured private management channels.

The command performs a bounded repeatable-read, read-only database transaction.
It returns no Steam IDs, provider IDs, authentication URLs, tokens or payloads.
Member mentions cannot ping. It does not complete registrations or modify
accounts, mileage, roles, achievements, Company Mods access or production data.
Permission and database errors return generic messages without member details.

Tests cover management access, wrong guild, public or leaked channels, missing
configuration, production exclusion, pagination, status consistency, provider
identifier redaction and preservation against disposable PostgreSQL.

Live channel acceptance requires an existing private management channel ID from
the owner. No Discord role or channel access is automatically granted.
