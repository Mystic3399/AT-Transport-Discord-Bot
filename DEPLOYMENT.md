# A&T Transport LTD — Driver Profile deployment

## Repository paths

Copy the delivered files into these exact repository locations:

```text
bot.py
assets/
├── badges/
│   ├── manifest.json
│   └── [22 existing progression badge PNGs]
└── profile/
    ├── driver-profile-template.png
    ├── icons/
    │   └── at-profile-icons.png
    └── prestige/
        └── at-prestige-insignias.png
```

The renderer resolves both asset directories relative to `bot.py`, so the
paths and lowercase filenames must remain unchanged on Railway's Linux
filesystem.

The renderer crops the icon and prestige atlases at runtime. They are local,
transparent PNGs, so no emoji font, network image host or platform-specific
glyph support is required.

## Python dependency

The profile renderer needs Pillow in the existing dependency file:

```text
Pillow>=10.0,<12
```

Keep the project's existing `discord.py`, `asyncpg`, `aiohttp` and
`beautifulsoup4` dependencies. No new database package or service is needed.

## Safe deployment

1. Back up the Railway PostgreSQL database using the normal project process.
2. Replace `bot.py` and add the `assets` files without changing Railway
   variables or the database volume/service.
3. Do **not** run a mileage import, repair, reset or historical replay for this
   release. The profile feature only reads existing verified records.
4. Deploy normally and check the log for the existing startup/schema messages.
5. In Discord, run `!profile` and `!profile @Driver` for a linked driver.
6. Confirm the fallback embed appears if a badge/template is deliberately made
   unavailable in a staging copy.
7. Confirm the existing website slash command still responds. This profile is
   a prefix command and does not replace or resync the website command.

## Data used by the profile

The image reads the existing `driver_links`, `driver_progress`,
`processed_jobs`, `permanent_achievements`, `hall_of_fame` and
`competition_wins` tables. Real-job totals are filtered to verified
TrucksBook `Real` records. Rendering does not update mileage, achievements,
competition history or the Immortal registry.

Convoy UI is intentionally absent while that project is paused. No member
since/join date is shown because the source does not have a reliable profile
join-date field.

## Rollback

Restore the previous `bot.py` and asset files, then redeploy. No profile-specific
database migration is introduced, so rollback does not require a data change.
