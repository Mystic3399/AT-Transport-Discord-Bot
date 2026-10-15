# A&T Command — World of Trucks mileage hotfix

Cause: TrucksBook emits `Statistics: Real, WoTr` for event deliveries. The previous exact comparison to `Real` discarded those jobs before database insertion. This is a bot classification bug, not evidence that TrucksBook marked the jobs Invalid.

The fix accepts `Real` and `Real, WoTr` (case/spacing/order tolerant). Race, Invalid, unknown classifications, and WoTr without Real remain excluded. Both live delivery processing and the existing mileage audit use the same classification rule. No database schema or existing totals are reset.

## Deployment

Deploy the reviewed hotfix branch through the existing Railway service, retaining its environment variables and PostgreSQL database. Wait for the bot to reconnect. A new Real, WoTr job should log `ACTION: Real mileage added.` Existing role, achievement and leaderboard processing remains in use. No live deployment has been verified by the author of this hotfix.

## Recover missed jobs

Take a PostgreSQL backup. In each original TrucksBook logbook channel, use the existing TrucksBook webhook ID (Discord Server Settings → Integrations → Webhooks, copy its URL; the numeric segment after `/webhooks/` is the ID). Do not share the webhook URL/token.

Preview the latest 1,000 channel messages:

```
!wotrecover WEBHOOK_ID 1000
```

After reviewing the preview:

```
!wotrecover WEBHOOK_ID 1000 CONFIRM
```

Replace WEBHOOK_ID with the actual numeric ID. Administrator permission is required. The bot needs View Channel and Read Message History. Increase the message limit up to 100000 if needed; the limit counts all channel messages. Repeat in ETS2 and ATS logbook channels as needed, specifying the correct webhook ID for each.

Only single-embed messages from that webhook, explicitly labelled Real + WoTr, with a positive accepted distance and a linked driver are replayed. Existing job IDs are skipped. Missing jobs reuse the normal transaction, totals, ranks, achievements and leaderboard path; reruns do not add the same job twice. Concurrent live deliveries may already credit a job while recovery runs; reported submitted miles are not a separate transaction receipt. Newly earned milestones may announce through the normal bot flow. Unlinked drivers are reported and can be linked before retrying. Deleted messages cannot be recovered through Discord history. Older multi-embed messages require separate review.

Screenshot examples: TruckTan jobs 68007306 (103 mi) and 68007834 (153 mi) qualify: 256 mi combined, if not already recorded. Earlier missed jobs are additional; this is not the complete event total.

## Validation

`python -m unittest discover -s tests -v` — five tests passed: Real/event label acceptance; invalid/race exclusions; screenshot job parsing; live-handler forwarding; recovery preview, apply, retry, deduplication and webhook/channel scope. `python -m py_compile bot.py` passed. Tests isolate bot functions without credentials or production database access. Live Discord/Railway/PostgreSQL integration still requires deployment verification.

Rollback: redeploy the preceding code version. Credited legitimate mileage remains in PostgreSQL; do not restore an older database merely to roll back code.
