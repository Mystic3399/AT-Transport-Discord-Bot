# Tachograph driver profile

!profile and !profile @Driver display the front of the driver card. View Back and View Front update the same Discord message, reload the driver's existing credited mileage and retain the displayed driver's identity. Persistent button routing is registered at startup.

Use /change country country:Australia with autocomplete, or !change country Australia. England is only an example; each driver enters their own country. /country and !country remain supported aliases. Use CLEAR to remove the selection. Country is self-reported and is independent of account verification. Empty values show NOT SET.

The additive driver_card_preferences table stores country and truck by Discord user ID. Truck allocation is serialised with a PostgreSQL transaction advisory lock, chooses randomly among the least-used available models, and never overwrites an existing assignment. Existing linked drivers are allocated on startup and new links within five minutes. Opening a profile allocates immediately if necessary. This table has no membership deletion cascade.

Deploy this change on top of fix/dual-provider-mileage-desktop. It uses that branch's existing profile data reader and does not recalculate mileage. The schema is created idempotently during startup. Discord slash commands sync through the existing startup flow. discord.py 2.4 or later is required for persistent dynamic buttons.

The artwork is in assets/tachograph. The renderer loads the shield and country archives directly, avoiding a runtime extraction step. Truck artwork remains the generated portrait set. Steam display names are cached separately from verification in driver_card_steam_names, retrieved from the matched Steam Community profile ID. Names refresh with the provider-name task; failed or private profile lookups retain the cache and display Name unavailable if no name has been retrieved. Numeric IDs remain internal. Drivers Hub shows Not linked until a supported account source supplies it.

Validation: pytest tests includes rendering rank boundaries, every truck, both shield states, country image dimensions, saved allocation reuse, balanced allocation and button reconstruction after restart. Live PostgreSQL migration and an actual Discord round trip still require deployment verification.


The approved front displays credited Trucky Race mileage in its existing TRUCKY RACE counter. Front/Back is the only card navigation; there is no separate Race view or button.

On startup and every five minutes, when the existing linked Trucky mileage feature is enabled in production, Race roles mirror the Real rank, achievement and Hall of Fame thresholds. These are separate roles with a Race Mileage suffix and no permissions. Only the highest Race rank and Hall tier are retained; earned Race achievements are cumulative and persisted. Role IDs and earned records use additive driver_race_role_map and driver_race_achievements tables. The worker uses a PostgreSQL advisory lock to prevent duplicate setup during deployment overlap. It never modifies existing Real roles, mileage totals, Real career records or Company Mods access. The 40,000 Real OR 60,000 Trucky Race Company Mods rule remains under its existing implementation.

TrucksBook and Trucky names come from their linked records. Discord uses the member display name. Steam shows its cached profile name rather than the numeric ID. The Drivers Hub row remains Not linked until an authenticated Hub identity integration is available.


Profile refinements: current rank uses a cyan silhouette glow on both faces. Earned milestones use their distinct supplied insignias; locked milestones remain grey with a lock. The map and wolf use feathered screen blending over the original background. The company role field selects actual company job titles and ignores decorative divider roles. Linked TrucksBook and Trucky display names refresh at startup and every 15 minutes using stable account IDs; failed lookups retain the last known name. Discord display names are read live at each render. These display refreshes never relink or reverify an identity and never write mileage totals.

Slash commands retry every minute until successful. Global duplicate removal restores in-memory command definitions even on an API failure, preserving register, myregistration and change country across reconnects. Discord authorisation and channel/role permissions are still enforced.


Additional presentation corrections: the front displays the highest permanently earned club (40K Mileage, 100K Centurion, 250K Veteran, 500K Elite, 750K Horizon Elite, 1M Immortal). Its driver shield is contained inside the status panel. Back Career Record values and progress totals are centred. Permanent achievements use six separate 74-pixel slots with fitted insignias, clipped glow and centred labels. All 22 locked shields derive from their complete unlocked silhouettes, with grayscale treatment and a separate padlock; they retain the full outer frame. The Steam display-name cache is additive and matched to the currently verified Steam ID on every read; name refreshes cannot relink or reverify accounts.
