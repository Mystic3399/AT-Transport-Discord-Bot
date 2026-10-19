# TrucksBook V2 ownership verification

## Provider capability and limitation

TrucksBook's [public registration page](https://trucksbook.eu/registration)
describes signing in through Steam to
register and verify game ownership. Its public company tools include employee
lists and CSV exports. The integration currently has no documented official
TrucksBook API that returns a stable TrucksBook account ID together with the
Steam identity that authenticated that account. A profile ID, public profile,
company roster entry, CSV row, Discord webhook author, or matching display
name is therefore only a claim or job-source signal; none independently proves
that the Discord applicant owns the provider account.

This method uses no Premium feature. The witnessed Steam sign-in is a manual
fallback, not a provider-signed API assertion. If the reviewer cannot see and
compare the canonical SteamID64 and resulting TrucksBook profile ID during the
same fresh sign-in, keep the claim pending and request authoritative confirmation
from TrucksBook support. Do not infer identity from similar names.

## Driver submission

1. Start registration in the private A&T registration channel.
2. Complete `!linksteam` first.
3. Submit `!registertrucksbook <numeric profile ID>`.
4. The bot stores a pending claim only. The profile remains unlinked until a
   reviewer records sufficient ownership and active A&T membership evidence.
5. Drivers must never share passwords, Steam Guard codes, recovery codes, or
   session cookies with A&T staff.

## Staff witnessed review

1. Arrange a private screenshare. The driver signs out of TrucksBook, then uses
   TrucksBook's official Steam sign-in with the same Steam account verified in
   their A&T profile.
2. During the same live session, staff checks the canonical SteamID64 against
   the stored Steam-verified identity, the resulting TrucksBook profile ID
   against the pending claim, and active A&T company membership through the
   authenticated company view.
3. Keep a minimal review record in the approved private company evidence store.
   Do not upload session data or credentials to Discord. Compute the SHA-256
   digest of the retained evidence file locally; only the digest is stored by
   the bot.
4. In the applicant's private channel, an authorised verification staff member
   runs:

   ```text
   !approveprovider trucksbook <observed SteamID64> <observed TrucksBook ID> <evidence SHA-256> confirm-member
   ```

5. The bot compares both observed IDs to the claim and verified Steam link,
   enforces unique account ownership, and records reviewer, time, evidence kind,
   and digest. Mismatch, missing Steam verification, duplicate ownership, or
   missing evidence leaves the claim unlinked/pending.

Approval only links the accounts and activates the identity profile. It does
not award mileage, alter legacy totals, issue roles, grant achievements, or
unlock Company Mods. Those remain behind separate release gates.

## Production restore status

The production backup bucket is present, but the connected Railway operations
interface does not expose object listing, object download, checksum, or restore
operations. Do not fetch bucket credentials into chat or restore over production
or the active staging database. A production-data restore rehearsal remains
blocked until the owner retrieves the existing logical backup through Railway's
private S3-compatible bucket using credentials kept locally and supplies it to
an isolated PostgreSQL restore environment, or grants a controlled tool access
to that already isolated restore environment.
