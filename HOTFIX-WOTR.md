# A&T Command — World of Trucks mileage hotfix

Cause: TrucksBook emits `Statistics: Real, WoTr` for event deliveries. The previous exact comparison to `Real` discarded those jobs before database insertion. This is a bot classification bug, not evidence that TrucksBook marked the jobs Invalid.

The fix accepts `Real` and `Real, WoTr` (case/spacing/order tolerant). Race, Invalid, unknown classifications, and WoTr without Real remain excluded. Both live delivery processing and the existing mileage audit use the same classification rule. No database schema or existing totals are reset.

## Deployment

Deploy the reviewed hotfix branch through the existing Railway service, retaining its environment variables and PostgreSQL database. Wait for the bot to reconnect. A new Real, WoTr job should log `ACTION: Real mileage added.` Existing role, achievement and leaderboard processing remains in use. No live deployment has been verified by the author of this hotfix.

## Recover missed jobs — existing commands

After the corrected deployment is running, take a PostgreSQL backup and run:

```
!mileageaudit
```

This scans readable server channels and stages missing, linked `Real, WoTr` deliveries alongside existing distance corrections. The preview shows missing event jobs staged, unlinked event jobs skipped and total pending mileage. Nothing is credited during the audit. Ordinary absent historical Real jobs remain excluded because they may already be included in imported baseline totals. The absence count is therefore not a promise that every absent job will be credited.

After reviewing the preview:

```
!mileageapply CONFIRM
```

Administrator permission is required. Existing job IDs, including deliveries credited while recovery runs, cannot be credited twice. Original Discord delivery timestamps are retained for historical leaderboard periods. Existing totals, progression, permanent awards, achievement repair announcements and leaderboard refresh remain in the established repair workflow. Re-running the audit and apply does not credit the same job again. Link any reported unlinked drivers before repeating the audit. Deleted source messages cannot be recovered through Discord history.

The prior `!wotrecover` command remains compatible, but is unnecessary for the standard repair process. Running it without arguments directs the administrator to the audit/apply workflow.

An additive startup migration adds repair type and source delivery time to the existing candidate table; no existing mileage, job or account data is removed. Existing candidates default to ordinary distance corrections.

Screenshot examples: TruckTan jobs 68007306 (103 mi) and 68007834 (153 mi) qualify: 256 mi combined, if not already recorded. Earlier missed jobs are additional; this is not the complete event total.

## Validation

`python -m unittest discover -s tests -v` — six isolated tests passed, including classification, screenshot examples, live delivery forwarding, standalone replay, and the full audit/apply flow with preview-only behavior, existing distance corrections, baseline exclusion, unlinked-driver handling, timestamp preservation and repeat/live-delivery protection. `python -m py_compile bot.py` and `git diff --check` passed. Tests do not connect to production. Deployment success and database startup migration must be confirmed in Railway logs. Actual mileage changes require the administrator to run the audit and apply commands.

Rollback: redeploy the preceding code version. Added candidate columns remain compatible. Credited legitimate mileage remains in PostgreSQL; do not restore an older database merely to roll back code.
