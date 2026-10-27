# Reviewing mileage repairs

Server administrators can use these commands in A&T Command's configured server:

1. `!mileageaudit` previews eligible TrucksBook repairs for linked drivers.
2. `!mileageaudit @Driver` previews only that driver's eligible repairs.
3. Review each driver's current total, positive addition, proposed total and job count. The attached `mileageaudit-preview.json` lists the exact job IDs and positive deltas in that batch.
4. Run `!mileageaudit CONFIRM` in the same channel, from the same administrator account, within 20 minutes.

The confirmation applies only the saved preview. `!mileageapply CONFIRM` remains a compatible confirmation entry point and requires the same preview. A changed balance, driver account link, job record or expired preview requires another audit. A consumed preview cannot be used twice. Other candidates discovered later are not included silently.

The audit uses the explicitly configured TrucksBook webhook sources. Each approved repair must match the driver's stored numeric TrucksBook account ID and explicit whole-mile source data. Unknown accounts, unsupported units and ambiguous historical records remain pending. Mentioning a member selects an existing linked driver; it does not establish ownership, reassign another driver's jobs or alter registration.

An unexplained balance is already part of the current driver total. It is never added again as missing mileage. The command repairs the existing legacy TrucksBook job ledger; it does not import all provider export totals, approve Trucky duplicates or enable the V2 award engine.

Staging confirmation is disabled. Legacy confirmation is also disabled if the production V2 award engine is active, preventing parallel award writers. Candidate, credit and approval-consumption updates share the existing repair transaction. Failed transactions leave the preview available to retry; Discord updates run only after committed mileage.
